"""
STL -> STEP Konverter ("Flaechenrueckfuehrung light") - v1.1

Ablauf:
1. STL laden (per trimesh), optional glaetten (Laplacian) und/oder
   vereinfachen (Dezimierung), dann als Zwischen-STL exportieren.
2. Mit OpenCASCADE (OCP) einlesen, alle Dreiecke zu einer Huelle
   vernaehen (Sewing).
3. Geschlossene Huelle -> ein Volumenkoerper (das gesamte Netz wird
   zu EINEM Koerper).
4. Optional: benachbarte, exakt in der gleichen Ebene liegende
   Dreiecke zu jeweils einer grossen echten Flaeche zusammenfassen
   (UnifySameDomain). Das betrifft ausschliesslich ebene Bereiche.
5. Zusaetzlich (rein informativ, siehe unten): gekruemmte
   Netzbereiche werden per Regionenwachstum + RANSAC-Fit erkannt und
   als Zylinder/Kugel gemeldet (Radius, grobe Guete). Diese Bereiche
   bleiben aber als Facetten im Ergebnis erhalten.
6. STEP schreiben. Zusaetzlich wird das Ergebnis fuer die
   Web-Vorschau erneut trianguliert und als STL exportiert.

Ehrlicher Stand zur Kruemmungs-Anfrage ("es sollen auch Kruemmungen
umgewandelt werden"): Ich habe versucht, erkannte Zylinder/Kugeln
automatisch durch echte gekruemmte STEP-Flaechen zu ersetzen (Naht-
und Solid-Aufbau). Das ist in Tests an genau der Stelle gescheitert,
an der auch kommerzielle Reverse-Engineering-Tools ihren groessten
Aufwand betreiben: die Netzkanten am Rand einer gekruemmten Region
liegen nur naeherungsweise auf der idealen Flaeche, und das saubere
Zusammenfuegen (inkl. Naht bei zylindrischen Flaechen) erzeugte in
meinen Tests ungueltige Geometrie. Um keine kaputten STEP-Dateien
auszuliefern, bleibt das aktuell bei der Erkennung + Anzeige (Radius,
Trefferguete) - siehe CHANGELOG "Ideen fuer spaeter" fuer den
moeglichen naechsten Schritt.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

import warnings

import numpy as np
import trimesh

from OCP.StlAPI import StlAPI_Reader, StlAPI_Writer
from OCP.TopoDS import TopoDS_Shape, TopoDS
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopExp import TopExp_Explorer, TopExp
from OCP.TopAbs import TopAbs_SHELL, TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.collections import (
    IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as EdgeFaceMap,
)

try:
    import pyransac3d as pyrsc
except Exception:  # pragma: no cover - Erkennung ist optional
    pyrsc = None

ProgressCallback = Callable[[int, str], None]


@dataclass
class ConversionSettings:
    smoothing_iterations: int = 0          # 0-10, Laplace-Glaettung vor dem Vernaehen
    decimate_percent: int = 100             # 10-100, 100 = keine Vereinfachung
    merge_planar: bool = True               # ebene Facetten zu Flaechen zusammenfassen
    detect_curved_shapes: bool = True       # Zylinder/Kugeln erkennen (nur Info)

    @classmethod
    def from_form(cls, form: dict) -> "ConversionSettings":
        def _int(key, default, lo, hi):
            try:
                v = int(form.get(key, default))
            except (TypeError, ValueError):
                v = default
            return max(lo, min(hi, v))

        return cls(
            smoothing_iterations=_int("smoothing_iterations", 0, 0, 10),
            decimate_percent=_int("decimate_percent", 100, 10, 100),
            merge_planar=str(form.get("merge_planar", "1")) not in ("0", "false", "False"),
            detect_curved_shapes=str(form.get("detect_curved_shapes", "1")) not in ("0", "false", "False"),
        )


@dataclass
class DetectedShape:
    kind: str            # "Zylinder" oder "Kugel"
    radius: float
    inlier_ratio: float
    face_count: int


@dataclass
class ConversionResult:
    output_path: str
    preview_path: Optional[str]
    is_solid: bool
    face_count_before: int
    face_count_after: int
    volume: Optional[float]
    detected_shapes: list = field(default_factory=list)


class ConversionError(Exception):
    pass


# --------------------------------------------------------------------------
# Hilfsfunktionen (OCCT)
# --------------------------------------------------------------------------

def _count_faces(shape) -> int:
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    n = 0
    while exp.More():
        n += 1
        exp.Next()
    return n


def _bounding_diagonal(shape) -> float:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    dx = box.GetXMax() - box.GetXMin()
    dy = box.GetYMax() - box.GetYMin()
    dz = box.GetZMax() - box.GetZMin()
    return max((dx ** 2 + dy ** 2 + dz ** 2) ** 0.5, 1e-6)


def _face_points(face) -> list:
    pts, seen = [], set()
    vexp = TopExp_Explorer(face, TopAbs_VERTEX)
    while vexp.More():
        v = TopoDS.Vertex(vexp.Current())
        p = BRep_Tool.Pnt_s(v)
        key = (round(p.X(), 6), round(p.Y(), 6), round(p.Z(), 6))
        if key not in seen:
            seen.add(key)
            pts.append(np.array([p.X(), p.Y(), p.Z()]))
        vexp.Next()
    return pts


def _face_normal(pts: list) -> np.ndarray:
    if len(pts) < 3:
        return np.array([0.0, 0.0, 1.0])
    n = np.cross(pts[1] - pts[0], pts[2] - pts[0])
    norm = np.linalg.norm(n)
    return n / norm if norm > 1e-12 else np.array([0.0, 0.0, 1.0])


def _detect_curved_shapes(shape, min_faces: int = 20) -> list:
    """Rein informative Erkennung von Zylinder-/Kugelregionen im Netz.

    Aendert die Geometrie NICHT - liefert nur eine Liste erkannter
    Formen fuer die Anzeige auf der Weboberflaeche.
    """
    if pyrsc is None:
        return []

    faces = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        faces.append(TopoDS.Face(exp.Current()))
        exp.Next()

    if len(faces) > 20000:
        # Bei sehr grossen Netzen lohnt sich die (recht teure) Analyse
        # in dieser einfachen Implementierung nicht mehr - ueberspringen
        # statt die Konvertierung unnoetig auszubremsen.
        return []

    face_pts = [_face_points(f) for f in faces]
    normals = [_face_normal(p) for p in face_pts]

    edge_face_map = EdgeFaceMap()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, edge_face_map)

    adjacency = {i: [] for i in range(len(faces))}
    for i in range(1, edge_face_map.Extent() + 1):
        flist = list(edge_face_map.FindFromIndex(i))
        if len(flist) != 2:
            continue
        idxs = []
        for fa in flist:
            fa_face = TopoDS.Face(fa)
            for j, ff in enumerate(faces):
                if ff.IsSame(fa_face):
                    idxs.append(j)
                    break
        if len(idxs) == 2:
            adjacency[idxs[0]].append(idxs[1])
            adjacency[idxs[1]].append(idxs[0])

    angle_thresh = np.cos(np.radians(30))
    visited = [False] * len(faces)
    regions = []
    for i in range(len(faces)):
        if visited[i]:
            continue
        stack, region = [i], []
        visited[i] = True
        while stack:
            cur = stack.pop()
            region.append(cur)
            for nb in adjacency[cur]:
                if not visited[nb] and np.dot(normals[cur], normals[nb]) > angle_thresh:
                    visited[nb] = True
                    stack.append(nb)
        regions.append(region)

    detected = []
    for region in regions:
        if len(region) < min_faces:
            continue

        pts = np.array([p for i in region for p in face_pts[i]])
        if len(pts) < 12:
            continue

        region_extent = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
        if region_extent < 1e-9:
            continue

        # Echte Kruemmung von blossem Glaettungs-/Vernetzungsrauschen
        # unterscheiden: Abstand der Punkte von der besten Ausgleichs-
        # ebene ("Pfeilhoehe") ins Verhaeltnis zur Ausdehnung der
        # Region setzen. Nur bei spuerbarer Woelbung weitermachen -
        # sonst wuerden leicht verrauschte, eigentlich ebene Bereiche
        # (z. B. nach Glaettung) faelschlich als Zylinder/Kugel erkannt.
        centroid = pts.mean(axis=0)
        _, _, vt = np.linalg.svd(pts - centroid)
        plane_normal = vt[-1]
        deviations = np.abs((pts - centroid) @ plane_normal)
        sagitta = float(deviations.max())
        if sagitta < max(1e-3, region_extent * 0.05):
            continue  # schon (fast) eben -> das erledigt UnifySameDomain

        # Toleranz an der Region selbst festmachen (nicht am ganzen
        # Modell) - sonst werden bei grossen Bauteilen auch minimal
        # verrauschte, eigentlich ebene Bereiche faelschlich als
        # Rundung erkannt.
        thresh = max(1e-3, region_extent * 5e-3)
        best = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            try:
                _, _, radius_cyl, inliers_cyl = pyrsc.Cylinder().fit(pts, thresh=thresh, maxIteration=200)
                ratio_cyl = len(inliers_cyl) / len(pts)
                if ratio_cyl > 0.85:
                    best = DetectedShape("Zylinder", float(radius_cyl), ratio_cyl, len(region))
            except Exception:
                pass

            try:
                _, radius_sph, inliers_sph = pyrsc.Sphere().fit(pts, thresh=thresh, maxIteration=200)
                ratio_sph = len(inliers_sph) / len(pts)
                if ratio_sph > 0.85 and (best is None or ratio_sph > best.inlier_ratio):
                    best = DetectedShape("Kugel", float(radius_sph), ratio_sph, len(region))
            except Exception:
                pass

        if best is not None:
            detected.append(best)

    detected.sort(key=lambda d: -d.face_count)
    return detected[:20]


# --------------------------------------------------------------------------
# Mesh-Vorverarbeitung (trimesh)
# --------------------------------------------------------------------------

def _preprocess_mesh(input_path: str, settings: ConversionSettings, tmp_path: str, report) -> str:
    if settings.smoothing_iterations <= 0 and settings.decimate_percent >= 100:
        return input_path

    prefix = "Netz wird vorbereitet"
    mesh = trimesh.load(input_path, force="mesh")

    if settings.smoothing_iterations > 0:
        report(6, f"{prefix}: Glaettung ({settings.smoothing_iterations}x) ...")
        # Taubin- statt reiner Laplace-Glaettung: entfernt Netzrauschen,
        # ohne das Modell sichtbar zu schrumpfen oder eigentlich ebene
        # Bereiche (z. B. Deckflaechen) in Woelbungen zu verwandeln.
        trimesh.smoothing.filter_taubin(mesh, iterations=settings.smoothing_iterations)

    if settings.decimate_percent < 100:
        target = max(4, int(len(mesh.faces) * settings.decimate_percent / 100))
        report(9, f"{prefix}: Vereinfachung auf {settings.decimate_percent}% ({target} Dreiecke) ...")
        mesh = mesh.simplify_quadric_decimation(face_count=target)

    mesh.export(tmp_path)
    return tmp_path


# --------------------------------------------------------------------------
# Hauptfunktion
# --------------------------------------------------------------------------

def convert_stl_to_step(
    input_path: str,
    output_path: str,
    preview_path: Optional[str] = None,
    settings: Optional[ConversionSettings] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> ConversionResult:
    settings = settings or ConversionSettings()

    def report(pct: int, msg: str) -> None:
        if progress_cb:
            progress_cb(pct, msg)

    try:
        report(2, "Lese STL-Datei ein ...")

        preprocessed_path = input_path
        if settings.smoothing_iterations > 0 or settings.decimate_percent < 100:
            preprocessed_path = _preprocess_mesh(
                input_path, settings, input_path + "_pre.stl", report
            )

        shape = TopoDS_Shape()
        reader = StlAPI_Reader()
        if not reader.Read(shape, preprocessed_path):
            raise ConversionError("STL-Datei konnte nicht gelesen werden.")

        faces_before = _count_faces(shape)
        if faces_before == 0:
            raise ConversionError("Die STL-Datei enthaelt keine Dreiecke.")

        report(15, f"Netz eingelesen ({faces_before} Dreiecke). Vernaehe Facetten ...")

        tol = _bounding_diagonal(shape) * 1e-5
        sewing = BRepBuilderAPI_Sewing(tol)
        sewing.Add(shape)
        sewing.Perform()
        sewed = sewing.SewedShape()

        report(35, "Suche geschlossene Huelle ...")
        exp = TopExp_Explorer(sewed, TopAbs_SHELL)
        shells = []
        while exp.More():
            shells.append(TopoDS.Shell(exp.Current()))
            exp.Next()

        is_solid = False
        base_shape = sewed

        if len(shells) == 1:
            report(48, "Huelle geschlossen - erzeuge Volumenkoerper (ein Koerper) ...")
            try:
                maker = BRepBuilderAPI_MakeSolid(shells[0])
                if maker.IsDone():
                    solid = maker.Solid()
                    if BRepCheck_Analyzer(solid).IsValid():
                        base_shape = solid
                        is_solid = True
            except Exception:
                pass
        else:
            report(
                48,
                f"Netz ist nicht wasserdicht ({len(shells)} Teil-Huellen) - "
                "Ergebnis wird als offene Flaeche gespeichert.",
            )

        detected_shapes = []
        if settings.detect_curved_shapes:
            report(58, "Erkenne gekruemmte Bereiche (Zylinder/Kugeln) ...")
            try:
                detected_shapes = _detect_curved_shapes(base_shape)
            except Exception:
                traceback.print_exc()
                detected_shapes = []

        result_shape = base_shape
        if settings.merge_planar:
            report(72, "Fasse ebene Bereiche zu grossen Flaechen zusammen (Flaechenrueckfuehrung) ...")
            unify = ShapeUpgrade_UnifySameDomain(base_shape, True, True, True)
            unify.SetLinearTolerance(tol)
            unify.SetAngularTolerance(1e-3)
            unify.Build()
            result_shape = unify.Shape()

        faces_after = _count_faces(result_shape)

        volume = None
        if is_solid:
            report(85, "Pruefe Volumenkoerper ...")
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(result_shape, props)
            volume = props.Mass()

        report(90, "Schreibe STEP-Datei ...")
        writer = STEPControl_Writer()
        writer.Transfer(result_shape, STEPControl_AsIs)
        status = writer.Write(output_path)
        if status != IFSelect_RetDone:
            raise ConversionError("STEP-Datei konnte nicht geschrieben werden.")

        if preview_path:
            report(97, "Erzeuge Vorschau ...")
            try:
                BRepMesh_IncrementalMesh(result_shape, max(tol, 1e-3), False, 0.5, True)
                StlAPI_Writer().Write(result_shape, preview_path)
            except Exception:
                traceback.print_exc()
                preview_path = None

        report(100, "Fertig.")
        return ConversionResult(
            output_path=output_path,
            preview_path=preview_path,
            is_solid=is_solid,
            face_count_before=faces_before,
            face_count_after=faces_after,
            volume=volume,
            detected_shapes=detected_shapes,
        )
    except ConversionError:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise ConversionError(f"Unerwarteter Fehler bei der Umwandlung: {exc}") from exc
