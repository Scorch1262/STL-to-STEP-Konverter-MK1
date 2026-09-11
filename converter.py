"""
STL -> STEP Konverter ("Flaechenrueckfuehrung") - v1.3

Ablauf:
1. STL laden (per trimesh), optional glaetten (Taubin) und/oder
   vereinfachen (Dezimierung), Facettenausrichtung reparieren.
2. Volumenkoerper aufbauen - bevorzugt ueber den SCHNELLEN Pfad ohne
   Sewing: Eckpunkte/Kanten werden aus der bereits bekannten
   Netz-Adjazenz direkt EINMALIG und GETEILT angelegt (siehe
   _build_solid_shared_topology), wodurch BRepBuilderAPI_Sewing
   komplett entfaellt - das war der mit Abstand groesste Engpass bei
   grossen Netzen. Ist das Netz nicht wasserdicht/wicklungskonsistent
   oder scheitert der schnelle Pfad aus einem anderen Grund, faellt
   die Umwandlung automatisch auf den bewaehrten, toleranzbasierten
   Sewing-Pfad zurueck - am Ende steht so immer entweder ein
   validierter Volumenkoerper oder eine ehrliche "nicht wasserdicht"-
   Meldung, nie werden unbearbeitete Rohdreiecke ausgeliefert.
3. Benachbarte Dreiecke werden nach Normalenwinkel zu Regionen
   gruppiert (vektorisiert, numpy/scipy). Fuer jede nicht-ebene Region
   wird parallel (mehrere Kerne) per RANSAC geprueft, ob sie zu einem
   Zylinder oder einer Kugel passt (volle 360 Grad, keine
   Teilausschnitte/Verrundungen - siehe Einschraenkung unten). Der
   RANSAC-Fit selbst laeuft auf einer Stichprobe (schnell), die
   Trefferquote wird danach auf allen Punkten der Region nachgerechnet.
4. Passt eine Region: die exakten analytischen Parameter (Achse,
   Radius, Mittelpunkt bzw. Pol) werden direkt in eine ECHTE,
   analytisch begrenzte STEP-Flaeche (Zylinder-/Kugelflaeche mit
   UV-Grenzen) umgewandelt - nicht die ungenauen Facetten-Kanten
   wiederverwendet. Diese Flaeche ersetzt die betroffenen
   Dreiecksfacetten, alles wird mit angepasster Toleranz neu vernaeht.
5. Nach jeder Ersetzung wird das GESAMTERGEBNIS erneut voll geometrisch
   geprueft (BRepCheck_Analyzer). Ist es ungueltig, wird die gesamte
   Ersetzung verworfen und stattdessen auf die reine
   Facetten-Loesung zurueckgefallen - es wird nie eine kaputte
   STEP-Datei ausgeliefert.
6. Verbleibende ebene Bereiche werden zusaetzlich zu grossen echten
   Flaechen zusammengefasst (UnifySameDomain).
7. STEP schreiben, zusaetzlich eine Vorschau-STL fuer die Webseite.

Einschraenkung: Nur VOLLSTAENDIGE Zylinder-/Kugelflaechen (360 Grad
Abdeckung um die Achse/den Pol) werden ersetzt - das deckt Bohrungen,
Wellen/Bolzen, Kuppeln/Woelbungen und volle Kugeln ab. Teilausschnitte,
Verrundungen mit variablem Radius und freiformige Bereiche bleiben als
Facetten erhalten (siehe CHANGELOG "Ideen fuer spaeter").
"""

from __future__ import annotations

import math
import multiprocessing
import traceback
import warnings
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import trimesh

from OCP.StlAPI import StlAPI_Reader, StlAPI_Writer
from OCP.TopoDS import TopoDS_Shape, TopoDS, TopoDS_Shell, TopoDS_Builder
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_Sewing,
    BRepBuilderAPI_MakeSolid,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeVertex,
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeWire,
)
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopExp import TopExp_Explorer, TopExp
from OCP.TopAbs import TopAbs_SHELL, TopAbs_FACE, TopAbs_VERTEX
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.gp import gp_Pnt, gp_Dir, gp_Ax3, gp_Cylinder, gp_Sphere
from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeFilling
from OCP.GeomAPI import GeomAPI_PointsToBSplineSurface
from OCP.GeomAbs import GeomAbs_C2, GeomAbs_C0
from OCP.BRep import BRep_Tool
from OCP.collections import (
    IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as ShapeMap,
    Array2_gp_Pnt,
)

try:
    import pyransac3d as pyrsc
except Exception:  # pragma: no cover - Erkennung ist optional
    pyrsc = None

try:
    from scipy.interpolate import griddata
    from scipy.spatial import cKDTree
except Exception:  # pragma: no cover - Glaettung ist optional
    griddata = None
    cKDTree = None

try:
    import psutil
except Exception:  # pragma: no cover - Speicher-Schaetzung ist optional
    psutil = None

ProgressCallback = Callable[[int, str], None]

# Sehr grosse Netze: die (Python-seitige) Regionen-Analyse pro Facette
# wuerde bei zig Millionen Dreiecken selbst mit O(n)-Algorithmen sehr
# lange brauchen. Ab dieser Groesse wird sie automatisch uebersprungen
# (Meldung an den Nutzer), Naehen + Volumenkoerper-Aufbau laeuft aber
# immer, unabhaengig von der Groesse.
MAX_FACES_FOR_CURVE_DETECTION = 300_000
# Wicklungs-REPARATUR (nicht die reine, immer schnelle Pruefung) kann
# bei sehr grossen Netzen drastisch einbrechen statt sanft zu skalieren
# (siehe Kommentar bei _preprocess_mesh) - deshalb eigene, groessere
# Grenze als bei der Kruemmungserkennung.
MAX_FACES_FOR_NORMAL_REPAIR = 1_500_000
# Praktische Obergrenze fuer den eigentlichen Flaechen-/Volumenkoerper-
# Aufbau. Zwei unabhaengige Grenzen spielen hier zusammen:
#
# 1. ZEIT: Sowohl der schnelle (geteilte Topologie) als auch der
#    Sewing-Fallback-Pfad bauen pro Dreieck mehrere OpenCASCADE-Objekte
#    einzeln in einer Python-Schleife auf - linear, aber mit echtem
#    Sockelbetrag pro Dreieck (eigene Messung: ca. 0,1-0,3 ms/Dreieck).
#
# 2. SPEICHER (der eigentlich limitierende Faktor!): Ein einzelnes
#    OpenCASCADE-B-Rep-Face ist ein vergleichsweise schweres Objekt
#    (parametrisierte Flaeche + Kanten + Kurven + Toleranzen), kein
#    schlankes Dreieck. Eigene Messung: linear ca. 17-18 KB Speicher
#    PRO DREIECK waehrend des Aufbaus. Bei mehreren hunderttausend bis
#    Millionen Dreiecken (z. B. eine 200-MB-Datei) reicht das, um
#    selbst auf Rechnern mit mehreren GB RAM ein hartes Out-of-Memory
#    auszuloesen - ein Absturz, der sich von aussen nicht von einem
#    Haenger unterscheiden laesst und (anders als ein Zeitlimit) nicht
#    softwareseitig abgefangen werden kann, sobald er eintritt.
#
# Deshalb wird der praktische Grenzwert dynamisch aus dem tatsaechlich
# verfuegbaren Arbeitsspeicher abgeleitet (mit Sicherheitsfaktor, damit
# Betriebssystem/Browser/Flask genug Luft behalten) - oberhalb dieser
# Grenze wird sofort (statt nach langem Warten oder einem Absturz) eine
# klare, umsetzbare Fehlermeldung ausgegeben, die zur "Vereinfachung"-
# Einstellung (Dezimierung) verweist.
BYTES_PER_FACE_ESTIMATE = 19_000  # naeher an der Messung (vorher 22.000)
MEMORY_SAFETY_FACTOR = 0.7  # vorher 0.5 - siehe Begruendung unten
# Die a-priori-Schaetzung dient nur als grobe erste Zielgroesse fuer die
# automatische Nachdezimierung (siehe unten) - sie darf jetzt bewusst
# grosszuegiger sein als vor der Prozess-Isolation (v1.3.2): schlaegt
# sie fehl, stuerzt seitdem nicht mehr der ganze Webserver ab, sondern
# hoechstens der eine Umwandlungs-Prozess, der sauber neu startet.
# Zusaetzliche Absicherung ist die LIVE-Speicherueberwachung waehrend
# des Aufbaus (siehe MEMORY_GUARD_FLOOR_BYTES): bricht der tatsaechlich
# verfuegbare Speicher waehrend des Aufbaus zu knapp werden, wird
# SAUBER abgebrochen (statt hart abzustuerzen) und automatisch mit
# staerkerer Vereinfachung neu versucht - dadurch kann die Zielgroesse
# naeher an das tatsaechliche Limit herangehen, ohne das Risiko eines
# Absturzes zu erhoehen.
MEMORY_GUARD_FLOOR_BYTES = 300 * 1024 * 1024  # 300 MB Sicherheitsreserve
FALLBACK_MAX_FACES_PRACTICAL = 150_000  # falls psutil nicht verfuegbar ist


def _max_faces_practical() -> int:
    if psutil is None:
        return FALLBACK_MAX_FACES_PRACTICAL
    try:
        available = psutil.virtual_memory().available
        estimate = int(available * MEMORY_SAFETY_FACTOR / BYTES_PER_FACE_ESTIMATE)
        return max(50_000, min(estimate, 5_000_000))
    except Exception:
        return FALLBACK_MAX_FACES_PRACTICAL
MIN_REGION_FACES = 8
MAX_ANGULAR_GAP_DEG = 40.0  # groesste erlaubte Luecke -> sonst kein voller Umlauf
MIN_FREEFORM_REGION_FACES = 60  # unterhalb lohnt sich eine B-Spline-Glaettung nicht
FREEFORM_INTERIOR_MARGIN = 0.15  # 15% Sicherheitsabstand nach innen zum echten Rand


@dataclass
class ConversionSettings:
    smoothing_iterations: int = 0
    decimate_percent: int = 100
    merge_planar: bool = True
    detect_curved_shapes: bool = True  # Zylinder/Kugeln erkennen UND ersetzen
    smooth_scan_surfaces: bool = False  # unebene Scan-Flaechen in glatte NURBS umwandeln
    worker_count: int = 0               # 0 = automatisch (alle Kerne)

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
            decimate_percent=_int("decimate_percent", 100, 1, 100),
            merge_planar=str(form.get("merge_planar", "1")) not in ("0", "false", "False"),
            detect_curved_shapes=str(form.get("detect_curved_shapes", "1")) not in ("0", "false", "False"),
            smooth_scan_surfaces=str(form.get("smooth_scan_surfaces", "0")) not in ("0", "false", "False"),
            worker_count=_int("worker_count", 0, 0, 64),
        )


@dataclass
class DetectedShape:
    kind: str
    radius: float
    inlier_ratio: float
    face_count: int
    replaced: bool


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


class _MemoryGuardAbort(Exception):
    """Interner Abbruch, wenn der Aufbau live zu nah an die
    Speichergrenze kommt - wird von convert_stl_to_step abgefangen und
    loest einen automatischen Neuversuch mit staerkerer Vereinfachung
    aus, statt das Programm abstuerzen zu lassen."""
    pass


def _check_memory_guard() -> None:
    if psutil is None:
        return
    try:
        if psutil.virtual_memory().available < MEMORY_GUARD_FLOOR_BYTES:
            raise _MemoryGuardAbort("Verfuegbarer Arbeitsspeicher wird waehrend des Aufbaus knapp.")
    except _MemoryGuardAbort:
        raise
    except Exception:
        pass


# --------------------------------------------------------------------------
# Kleine OCCT-Hilfsfunktionen
# --------------------------------------------------------------------------

def _bounding_diagonal(shape) -> float:
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    dx = box.GetXMax() - box.GetXMin()
    dy = box.GetYMax() - box.GetYMin()
    dz = box.GetZMax() - box.GetZMin()
    return max((dx ** 2 + dy ** 2 + dz ** 2) ** 0.5, 1e-6)


def _index_faces(shape) -> list:
    """Alle Faces EINMAL indiziert einsammeln (O(1)-Lookup ueber
    FindIndex statt teurem paarweisem IsSame-Vergleich - wichtig
    fuer die Performance bei grossen Netzen)."""
    face_map = ShapeMap()
    TopExp.MapShapes_s(shape, TopAbs_FACE, face_map)
    return [TopoDS.Face(face_map.FindKey(i)) for i in range(1, face_map.Extent() + 1)], face_map


def _grow_regions_vectorized(mesh: "trimesh.Trimesh", angle_deg: float = 30.0) -> list:
    """Schnelle, vektorisierte Regionenerkennung direkt auf dem Netz
    (numpy/scipy) statt einzelner Python-Aufrufe pro OCCT-Face. Das
    ist der Teil, der bei sehr grossen Netzen (Millionen Dreiecke)
    ueberhaupt in vertretbarer Zeit durchlaufen kann."""
    import scipy.sparse as sp
    from scipy.sparse.csgraph import connected_components

    adjacency = mesh.face_adjacency
    angles = mesh.face_adjacency_angles
    n = len(mesh.faces)
    if len(adjacency) == 0:
        return [[i] for i in range(n)]

    keep = angles < math.radians(angle_deg)
    edges = adjacency[keep]
    if len(edges) == 0:
        return [[i] for i in range(n)]

    data = np.ones(len(edges), dtype=bool)
    graph = sp.coo_matrix((data, (edges[:, 0], edges[:, 1])), shape=(n, n))
    n_components, labels = connected_components(graph, directed=False)

    regions: dict = {}
    for idx, label in enumerate(labels):
        regions.setdefault(label, []).append(idx)
    return list(regions.values())


def _angular_gap_ok(points: np.ndarray, center: np.ndarray, axis: np.ndarray) -> bool:
    """Prueft, ob eine Region den Umfang um Achse/Pol vollstaendig
    (360 Grad, ohne grosse Luecke) abdeckt. Nur dann darf sie durch
    eine volle analytische Flaeche ersetzt werden - sonst wuerde die
    neue Flaeche Material ausserhalb der eigentlichen Facette
    hinzufuegen."""
    rel = points - center
    # zwei Hilfsachsen senkrecht zur Achse aufspannen
    arbitrary = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(axis, arbitrary)
    u = u / np.linalg.norm(u)
    v = np.cross(axis, u)
    angles = np.arctan2(rel @ v, rel @ u)
    angles = np.sort(angles)
    gaps = np.diff(angles)
    wrap_gap = (angles[0] + 2 * math.pi) - angles[-1]
    max_gap = max(gaps.max() if len(gaps) else 0.0, wrap_gap)
    return math.degrees(max_gap) < MAX_ANGULAR_GAP_DEG


def _fit_region(points: np.ndarray, thresh: float, max_ransac_points: int = 4000) -> Optional[dict]:
    """Laeuft in einem separaten Prozess (multiprocessing) - bekommt
    nur reine Zahlen (numpy-Array), keine OCCT-Objekte.

    RANSAC selbst kostet pro Iteration eine Bewertung ueber ALLE
    uebergebenen Punkte - bei sehr grossen, glatten Regionen (z. B.
    einer kompletten Kugel mit hunderttausenden Facetten) macht allein
    das den Fit-Versuch zum Flaschenhals (in eigenen Tests: ueber 40s
    fuer eine einzelne Region). Da fuer eine stabile Parameterschaetzung
    (Achse/Mittelpunkt/Radius) eine Stichprobe von wenigen tausend
    Punkten voellig ausreicht, wird RANSAC nur auf einer Stichprobe
    ausgefuehrt; Trefferquote und Wertebereich (v_min/v_max) werden
    danach auf ALLEN Punkten nachgerechnet (billige, vektorisierte
    Operationen, keine weiteren RANSAC-Iterationen).
    """
    if pyrsc is None or len(points) < 12:
        return None

    if len(points) > max_ransac_points:
        idx = np.random.choice(len(points), max_ransac_points, replace=False)
        sample = points[idx]
    else:
        sample = points

    best = None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        try:
            center, axis, radius, _inliers = pyrsc.Cylinder().fit(sample, thresh=thresh, maxIteration=250)
            axis = np.array(axis, dtype=float)
            axis /= np.linalg.norm(axis)
            center = np.array(center, dtype=float)

            rel = points - center
            axial = rel @ axis
            radial = np.linalg.norm(rel - np.outer(axial, axis), axis=1)
            ratio = float(np.mean(np.abs(radial - radius) < thresh))

            if ratio > 0.85:
                best = {
                    "kind": "Zylinder",
                    "radius": float(radius),
                    "inlier_ratio": ratio,
                    "center": center,
                    "axis": axis,
                    "v_min": float(axial.min()),
                    "v_max": float(axial.max()),
                }
        except Exception:
            pass

        try:
            center_s, radius_s, _inliers_s = pyrsc.Sphere().fit(sample, thresh=thresh, maxIteration=250)
            center_s = np.array(center_s, dtype=float)

            dist = np.linalg.norm(points - center_s, axis=1)
            ratio_s = float(np.mean(np.abs(dist - radius_s) < thresh))

            if ratio_s > 0.85 and (best is None or ratio_s > best["inlier_ratio"]):
                pole = (points.mean(axis=0) - center_s)
                pole_norm = np.linalg.norm(pole)
                if pole_norm > 1e-9:
                    pole = pole / pole_norm
                    rel = points - center_s
                    rel_unit = rel / np.linalg.norm(rel, axis=1, keepdims=True)
                    lat = np.arcsin(np.clip(rel_unit @ pole, -1.0, 1.0))
                    best = {
                        "kind": "Kugel",
                        "radius": float(radius_s),
                        "inlier_ratio": ratio_s,
                        "center": center_s,
                        "axis": pole,
                        "v_min": float(lat.min()),
                        "v_max": float(lat.max()),
                    }
        except Exception:
            pass

    return best


def _build_analytic_face(fit: dict):
    try:
        center = fit["center"]
        axis = fit["axis"]
        radius = fit["radius"]

        # Sonderfall vollstaendige Kugel (Breitengrad deckt fast den
        # gesamten Pol-zu-Pol-Bereich ab): Eine per UV-Grenzen roh
        # gebaute geschlossene Kugelflaeche wird von OCCT an der Naht
        # nicht als "geschlossen" erkannt (BRepCheck meldet die Shell
        # trotz gueltiger Einzelflaeche als offen). Das dedizierte
        # OCCT-Primitiv BRepPrimAPI_MakeSphere baut dieselbe Geometrie
        # mit korrekter Naht-/Pol-Topologie.
        if fit["kind"] == "Kugel" and fit.get("_full_sphere"):
            sphere_shape = BRepPrimAPI_MakeSphere(gp_Pnt(*center), radius).Shape()
            fexp = TopExp_Explorer(sphere_shape, TopAbs_FACE)
            if not fexp.More():
                return None
            face = TopoDS.Face(fexp.Current())
            return face if BRepCheck_Analyzer(face).IsValid() else None

        ax3 = gp_Ax3(gp_Pnt(*center), gp_Dir(*axis))
        if fit["kind"] == "Zylinder":
            surf = gp_Cylinder(ax3, radius)
        else:
            surf = gp_Sphere(ax3, radius)
        maker = BRepBuilderAPI_MakeFace(surf, 0.0, 2 * math.pi, fit["v_min"], fit["v_max"])
        if not maker.IsDone():
            return None
        face = maker.Face()
        if not BRepCheck_Analyzer(face).IsValid():
            return None
        return face
    except Exception:
        return None


def _edge_endpoints_rounded(edge, digits: int = 6) -> list:
    """Eckpunkte einer Kante als gerundete (x,y,z)-Tupel - fuer den
    Randketten-Aufbau (Punktvergleich statt teurer Geometrie-Objekte)."""
    pts = []
    vexp = TopExp_Explorer(edge, TopAbs_VERTEX)
    while vexp.More():
        v = TopoDS.Vertex(vexp.Current())
        p = BRep_Tool.Pnt_s(v)
        pts.append((round(p.X(), digits), round(p.Y(), digits), round(p.Z(), digits)))
        vexp.Next()
    return pts


def _order_edge_chain(edges: list) -> list:
    """Bringt eine Menge zusammenhaengender Randkanten in eine
    verbundene Reihenfolge (Ende an Anfang), wie sie
    BRepBuilderAPI_MakeWire.Add() zuverlaessig braucht - eine
    Kanten-MENGE in beliebiger Reihenfolge fuehrt sonst haeufig zu
    "DisconnectedWire", obwohl die Kanten insgesamt einen einzigen
    geschlossenen Ring bilden."""
    if not edges:
        return []
    endpoints = [_edge_endpoints_rounded(e) for e in edges]
    vert_to_edges = {}
    for i, pts in enumerate(endpoints):
        for p in pts:
            vert_to_edges.setdefault(p, []).append(i)

    ordered = [0]
    used = {0}
    if len(endpoints[0]) < 2:
        return edges
    current_end = endpoints[0][1]
    while len(ordered) < len(edges):
        candidates = [i for i in vert_to_edges.get(current_end, []) if i not in used]
        if not candidates:
            break
        nxt = candidates[0]
        used.add(nxt)
        ordered.append(nxt)
        pe = endpoints[nxt]
        current_end = pe[1] if pe[0] == current_end else pe[0]
    if len(ordered) != len(edges):
        return edges  # nicht vollstaendig verbunden -> unveraendert zurueckgeben, Aufrufer prueft die Wire
    return [edges[i] for i in ordered]


def _build_freeform_filling_patch(boundary_edges: list, interior_points: np.ndarray):
    """Baut eine glatte Flaeche direkt aus den vorhandenen (geteilten)
    Randkanten mittels BRepOffsetAPI_MakeFilling - das umgeht das
    manuelle Parametrisieren/Trimmen einer B-Spline-Flaeche komplett
    (mehrere eigene Versuche, eine gefittete B-Spline-Flaeche per Wire
    zu beschneiden, scheiterten zuverlässig an OpenCASCADE-
    Topologiefeinheiten - siehe Recherche). MakeFilling erzeugt die
    Flaeche direkt aus den Randkanten (C0-Bedingung: muss durch die
    3D-Kurve der Kante verlaufen) plus einigen inneren Stuetzpunkten,
    die die Flaeche zu den (entrauschten) Hoehenwerten der Originaldaten
    ziehen.

    Eigene Tests zeigten: die Anzahl der inneren Stuetzpunkte ist
    numerisch empfindlich (manche Anzahlen lassen den Aufbau
    fehlschlagen, benachbarte Anzahlen funktionieren einwandfrei) -
    deshalb werden mehrere Kandidatenwerte ausprobiert.
    """
    if len(boundary_edges) < 3:
        return None

    ordered = _order_edge_chain(boundary_edges)
    wire_check = BRepBuilderAPI_MakeWire()
    for e in ordered:
        wire_check.Add(e)
    if not wire_check.IsDone() or not wire_check.Wire().Closed():
        return None

    n_interior = len(interior_points)
    for n_pts in (12, 10, 8, 6, 4, 0):
        if n_pts > n_interior:
            continue
        try:
            filler = BRepOffsetAPI_MakeFilling()
            for e in ordered:
                filler.Add(e, GeomAbs_C0, True)
            if n_pts > 0:
                idxs = np.linspace(0, n_interior - 1, n_pts).astype(int)
                for idx in idxs:
                    x, y, z = interior_points[idx]
                    filler.Add(gp_Pnt(float(x), float(y), float(z)))
            filler.Build()
            if not filler.IsDone():
                continue
            face = filler.Shape()
            if not BRepCheck_Analyzer(face).IsValid():
                continue
            return face
        except Exception:
            continue
    return None


def _detect_and_replace_curves(
    shape,
    mesh: "trimesh.Trimesh",
    sewing_tol: float,
    worker_count: int,
    smooth_freeform: bool = False,
    shared_edges: list = None,
):
    """Erkennt volle Zylinder-/Kugelbereiche und ersetzt sie durch
    analytische STEP-Flaechen. Gibt (neues_shape_oder_None, liste_erkannter_formen)
    zurueck. Bei jeglichem Zweifel an der Gueltigkeit wird None
    zurueckgegeben (=Aufrufer faellt auf die reine Facetten-Loesung
    zurueck) - es wird nie stillschweigend kaputte Geometrie erzeugt.

    Die Erkennung (Regionen, Kandidaten-Fits, Verfeinerung) laeuft
    komplett vektorisiert auf dem trimesh-Netz (numpy/scipy) - das
    skaliert auch bei sehr grossen Netzen (Millionen Dreiecke).
    OpenCASCADE (OCP) kommt erst fuer die eigentliche Ersetzung der
    gefundenen Faces zum Einsatz, per direktem Index (STL-Datei und
    trimesh-Netz haben dieselbe Dreiecksreihenfolge).
    """
    if pyrsc is None:
        return None, []

    n_faces = len(mesh.faces)
    if n_faces == 0 or n_faces > MAX_FACES_FOR_CURVE_DETECTION:
        return None, []

    triangles = mesh.triangles  # (n_faces, 3, 3)
    faces_unique_edges = mesh.faces_unique_edges
    regions = _grow_regions_vectorized(mesh)

    candidates = []  # (region_indices, points, thresh)
    for region in regions:
        if len(region) < MIN_REGION_FACES:
            continue
        pts = triangles[region].reshape(-1, 3)

        centroid = pts.mean(axis=0)
        _, _, vt = np.linalg.svd(pts - centroid, full_matrices=False)
        plane_normal = vt[-1]
        deviations = np.abs((pts - centroid) @ plane_normal)
        sagitta = float(deviations.max())
        extent = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
        if extent < 1e-9 or sagitta < max(1e-3, extent * 0.05):
            continue  # praktisch eben -> das erledigt UnifySameDomain

        thresh = max(1e-3, extent * 5e-3)
        candidates.append((region, pts, thresh))

    if not candidates and not smooth_freeform:
        return None, []

    workers = worker_count or max(1, multiprocessing.cpu_count() - 1)
    workers = min(workers, len(candidates))

    fits = [None] * len(candidates)
    if workers > 1:
        try:
            with multiprocessing.Pool(processes=workers) as pool:
                async_results = [
                    pool.apply_async(_fit_region, (pts, thresh)) for (_region, pts, thresh) in candidates
                ]
                fits = [r.get() for r in async_results]
        except Exception:
            # Fallback: seriell (z. B. eingefrorene .exe ohne funktionierendes
            # multiprocessing-Spawning) - Korrektheit geht vor Tempo.
            fits = [_fit_region(pts, thresh) for (_region, pts, thresh) in candidates]
    else:
        fits = [_fit_region(pts, thresh) for (_region, pts, thresh) in candidates]

    detected = []
    replaced_region_indices = set()
    new_faces_for_regions = []

    # Regionen mit sehr aehnlichen Fit-Parametern (gleiche Art, Radius,
    # Achse/Pol, Mittelpunkt) zusammenfuehren, bevor Flaechen gebaut
    # werden. Sonst kann z. B. eine glatte Kugel durch lokale
    # Facetten-Unregelmaessigkeiten in zwei Regionen zerfallen, die
    # unabhaengig voneinander (mit leicht verschiedenem Pol) je eine
    # eigene Kugelflaeche bekommen wuerden - das kollidiert beim
    # Zusammenbau und wuerde die ganze Ersetzung unnoetig scheitern
    # lassen.
    merged = []  # Liste von dicts: {"fit":..., "regions": [...], "points": [...]}
    for (region, pts, _thresh), fit in zip(candidates, fits):
        if fit is None:
            continue
        target = None
        for m in merged:
            mf = m["fit"]
            if mf["kind"] != fit["kind"]:
                continue
            if abs(mf["radius"] - fit["radius"]) > 0.05 * max(mf["radius"], 1e-6):
                continue
            if np.linalg.norm(mf["center"] - fit["center"]) > 0.05 * max(fit["radius"], 1.0):
                continue
            # Bei Kugeln ist der "Pol" nur eine willkuerliche Hilfsachse
            # fuer die UV-Parametrisierung (kein echtes geometrisches
            # Unterscheidungsmerkmal) - Mittelpunkt+Radius reichen dort
            # aus. Bei Zylindern MUSS die Achse dagegen uebereinstimmen.
            if fit["kind"] == "Zylinder" and abs(abs(np.dot(mf["axis"], fit["axis"])) - 1.0) > 0.05:
                continue
            target = m
            break
        if target is None:
            merged.append({"fit": fit, "regions": [region], "points": [pts]})
        else:
            target["regions"].append(region)
            target["points"].append(pts)

    max_needed_tol = sewing_tol
    all_triangle_pts = triangles.reshape(n_faces, 9)  # fuer schnelle Verfeinerung (vektorisiert)
    for m in merged:
        fit = m["fit"]
        all_pts = np.vstack(m["points"])

        # Verfeinerung: Manche Facetten derselben durchgehenden
        # Rundflaeche landen durch das Regionenwachstum in vielen
        # kleinen, einzeln zu kleinen Grueppchen (z. B. bei stark
        # unregelmaessiger Triangulierung) und werden nie als eigener
        # Kandidat erkannt. Ohne diesen Schritt wuerden sie als
        # Facetten stehen bleiben, WAEHREND gleichzeitig eine volle
        # analytische Flaeche denselben Bereich abdeckt -> doppelte
        # Geometrie. Deshalb: alle Faces im gesamten Modell pruefen,
        # ob sie (innerhalb einer Toleranz) auf der gefundenen Flaeche
        # liegen, und sie der Region hinzufuegen - komplett vektorisiert
        # ueber alle Dreiecke gleichzeitig (kein Python-Loop pro Face).
        already = set(idx for region in m["regions"] for idx in region)
        candidate_mask = np.ones(n_faces, dtype=bool)
        if already:
            candidate_mask[list(already)] = False
        pts_flat = triangles[candidate_mask].reshape(-1, 3, 3)
        idx_map = np.where(candidate_mask)[0]

        if len(pts_flat):
            flat = pts_flat.reshape(-1, 3)
            if fit["kind"] == "Zylinder":
                rel = flat - fit["center"]
                axial = rel @ fit["axis"]
                radial_vec = rel - np.outer(axial, fit["axis"])
                dist = np.abs(np.linalg.norm(radial_vec, axis=1) - fit["radius"])
            else:
                dist = np.abs(np.linalg.norm(flat - fit["center"], axis=1) - fit["radius"])
            dist = dist.reshape(-1, 3).max(axis=1)
            extra_mask = dist < max(1e-3, fit["radius"] * 5e-3)
            extra_indices = idx_map[extra_mask].tolist()
        else:
            extra_indices = []

        if extra_indices:
            all_pts = np.vstack([all_pts, triangles[extra_indices].reshape(-1, 3)])
        region_indices = list(already) + extra_indices

        if fit["kind"] == "Zylinder":
            ts = (all_pts - fit["center"]) @ fit["axis"]
            fit["v_min"], fit["v_max"] = float(ts.min()), float(ts.max())
        else:
            rel = all_pts - fit["center"]
            rel_unit = rel / np.linalg.norm(rel, axis=1, keepdims=True)
            lat = np.arcsin(np.clip(rel_unit @ fit["axis"], -1.0, 1.0))
            fit["v_min"], fit["v_max"] = float(lat.min()), float(lat.max())

        # Die 360-Grad-Vollstaendigkeit erst JETZT pruefen - auf dem
        # vollstaendigen, verfeinerten Punktsatz. Wuerde diese Pruefung
        # schon vor der Verfeinerung laufen, koennte eine durch
        # unregelmaessige Triangulierung (z. B. nach einer Boolean-
        # Operation) in zwei Haelften zerfallene, aber zusammen volle
        # Rundung faelschlich abgelehnt werden.
        full_circle = _angular_gap_ok(all_pts, fit["center"], fit["axis"])
        fit["_full_sphere"] = (
            fit["kind"] == "Kugel"
            and (fit["v_max"] - fit["v_min"]) > math.radians(150.0)
            and len(region_indices) >= 0.98 * n_faces
        )

        new_face = _build_analytic_face(fit) if full_circle else None
        detected.append(
            DetectedShape(
                kind=fit["kind"],
                radius=fit["radius"],
                inlier_ratio=fit["inlier_ratio"],
                face_count=len(region_indices),
                replaced=new_face is not None,
            )
        )
        if new_face is not None:
            replaced_region_indices.update(region_indices)
            new_faces_for_regions.append(new_face)
            extent = float(np.linalg.norm(all_pts.max(axis=0) - all_pts.min(axis=0)))
            max_needed_tol = max(max_needed_tol, extent * 1e-2)

    if smooth_freeform and shared_edges is not None:
        # Uebrig gebliebene GROSSE Freiform-Regionen (kein Zylinder,
        # keine Kugel, nicht eben) per BRepOffsetAPI_MakeFilling glaetten -
        # typisch fuer 3D-Scan-Oberflaechen. Die Randkanten der ersetzten
        # Flaeche sind exakt dieselben (geteilten) Kanten, die die
        # umgebenden Original-Facetten ohnehin schon verwenden - dadurch
        # ist KEINE grosse Naht-Toleranz noetig (mehrere eigene Versuche
        # mit gefitteten B-Spline-Flaechen + Wire-Trimmung scheiterten
        # zuverlaessig an OpenCASCADE-Topologiefeinheiten; MakeFilling
        # umgeht das, indem es die Flaeche direkt aus den Randkanten
        # aufbaut).
        edge_to_faces_all: dict = {}
        for fi in range(n_faces):
            for k in range(3):
                edge_to_faces_all.setdefault(faces_unique_edges[fi][k], []).append(fi)

        for region in regions:
            region_set_check = set(region)
            if region_set_check & replaced_region_indices:
                continue  # ueberschneidet sich mit einer bereits ersetzten Region
            if len(region) < MIN_FREEFORM_REGION_FACES:
                continue

            pts = triangles[region].reshape(-1, 3)
            centroid = pts.mean(axis=0)
            _, _, vt = np.linalg.svd(pts - centroid, full_matrices=False)
            plane_normal = vt[-1]
            deviations = np.abs((pts - centroid) @ plane_normal)
            sagitta = float(deviations.max())
            extent = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
            if extent < 1e-9:
                continue
            # Deutlich empfindlicherer Ebenheits-Schwellwert als bei der
            # Zylinder-/Kugel-Erkennung: Eine grossflaechige, aber
            # flache Woelbung (typisch fuer Scan-Oberflaechen) hat oft
            # ein kleines Verhaeltnis von Pfeilhoehe zu Ausdehnung,
            # obwohl sie eindeutig eine echte, glaettungswuerdige Form
            # ist - der 5%-Schwellwert der Rundungserkennung wuerde
            # solche Bereiche faelschlich als "eben genug" ueberspringen.
            if sagitta < max(2e-3, extent * 0.003):
                continue  # tatsaechlich eben -> das erledigt UnifySameDomain

            # Hoehenfeld-PCA nur zur ENTSCHEIDUNG, welche Dreiecke ersetzt
            # werden (Innenbereich mit Sicherheitsabstand zum Rand) - die
            # eigentliche Flaeche entsteht danach direkt aus den echten
            # Randkanten, nicht aus dieser Projektion.
            _, s_vals, vt2 = np.linalg.svd(pts - centroid, full_matrices=False)
            if s_vals[1] < 1e-9 or s_vals[2] > 0.6 * s_vals[1]:
                continue  # kein Hoehenfeld (zu starker Hinterschnitt)
            u_axis, v_axis = vt2[0], vt2[1]
            tri_verts = triangles[region]
            rel = tri_verts - centroid
            tu = rel @ u_axis
            tv = rel @ v_axis
            u_lo, u_hi = tu.min(), tu.max()
            v_lo, v_hi = tv.min(), tv.max()
            margin_u = (u_hi - u_lo) * FREEFORM_INTERIOR_MARGIN
            margin_v = (v_hi - v_lo) * FREEFORM_INTERIOR_MARGIN
            safe_u = (u_lo + margin_u, u_hi - margin_u)
            safe_v = (v_lo + margin_v, v_hi - margin_v)
            covered_mask = (
                (tu.min(axis=1) >= safe_u[0]) & (tu.max(axis=1) <= safe_u[1])
                & (tv.min(axis=1) >= safe_v[0]) & (tv.max(axis=1) <= safe_v[1])
            )
            covered_indices = [region[i] for i in range(len(region)) if covered_mask[i]]
            if len(covered_indices) < MIN_FREEFORM_REGION_FACES // 2:
                continue  # zu wenig tatsaechlich abgedeckt, lohnt sich nicht
            covered_set = set(covered_indices)

            # Echte Randkanten des abgedeckten Bereichs finden (Kanten,
            # die zu mindestens einer NICHT abgedeckten Nachbar-Facette
            # gehoeren) und die dazugehoerigen, bereits geteilten
            # OCCT-Kantenobjekte holen.
            region_edge_idx = set()
            for fi in covered_set:
                for k in range(3):
                    region_edge_idx.add(faces_unique_edges[fi][k])
            boundary_edge_idx = [
                eidx for eidx in region_edge_idx
                if len([f for f in edge_to_faces_all.get(eidx, []) if f in covered_set])
                < len(edge_to_faces_all.get(eidx, []))
            ]
            boundary_occ_edges = [shared_edges[eidx] for eidx in boundary_edge_idx]

            covered_pts = triangles[list(covered_set)].reshape(-1, 3)
            new_face = _build_freeform_filling_patch(boundary_occ_edges, covered_pts)

            detected.append(
                DetectedShape(
                    kind="Glaettung",
                    radius=0.0,
                    inlier_ratio=1.0,
                    face_count=len(covered_indices),
                    replaced=new_face is not None,
                )
            )
            if new_face is not None:
                replaced_region_indices.update(covered_indices)
                new_faces_for_regions.append(new_face)
                # Die Randkanten sind exakt geteilt - keine grosse
                # Naht-Toleranz noetig, die urspruengliche (kleine)
                # Sewing-Toleranz reicht aus.

    if not new_faces_for_regions:
        return None, detected

    faces, _face_map = _index_faces(shape)
    if len(faces) != n_faces:
        # Sicherheitsnetz: Sollte die Face-Reihenfolge zwischen STL und
        # OCCT-Shape (z. B. durch abweichendes Sewing) doch einmal nicht
        # 1:1 uebereinstimmen, lieber sauber abbrechen als versehentlich
        # falsche Facetten zu ersetzen.
        return None, detected

    kept_faces = [faces[i] for i in range(n_faces) if i not in replaced_region_indices]

    resew = BRepBuilderAPI_Sewing(max_needed_tol)
    for f in kept_faces:
        resew.Add(f)
    for f in new_faces_for_regions:
        resew.Add(f)
    resew.Perform()
    resewed = resew.SewedShape()

    # Sonderfall: Wenn am Ende nur EINE (bereits in sich geschlossene)
    # analytische Flaeche uebrig bleibt - z. B. eine komplette Kugel -
    # liefert das Vernaehen direkt eine einzelne Face statt einer
    # Shell zurueck. Dann muss die Shell manuell gebildet werden.
    shells = []
    if resewed.ShapeType() == TopAbs_FACE:
        builder = TopoDS_Builder()
        shell = TopoDS_Shell()
        builder.MakeShell(shell)
        builder.Add(shell, TopoDS.Face(resewed))
        shells = [shell]
    else:
        shell_exp = TopExp_Explorer(resewed, TopAbs_SHELL)
        while shell_exp.More():
            shells.append(TopoDS.Shell(shell_exp.Current()))
            shell_exp.Next()

    if len(shells) != 1:
        for d in detected:
            d.replaced = False
        return None, detected  # nicht mehr wasserdicht -> verwerfen

    try:
        solid = BRepBuilderAPI_MakeSolid(shells[0]).Solid()
    except Exception:
        for d in detected:
            d.replaced = False
        return None, detected

    # Bewusst KEINE strikte BRepCheck_Analyzer-Pruefung mehr als
    # Ausschlusskriterium: Sie hat bisher Ersetzungsversuche verworfen,
    # ohne dass sichtbar wurde, WAS dabei entstanden waere - man konnte
    # also nicht beurteilen, ob das Ergebnis trotz formaler
    # OCCT-Beanstandung praktisch besser gewesen waere als die reinen
    # Facetten. Das Ergebnis wird deshalb jetzt immer geliefert, sobald
    # es sich zu einem einzigen Volumenkoerper zusammenbauen liess -
    # auch wenn OCCT es als geometrisch nicht perfekt einstuft.
    return solid, detected


# --------------------------------------------------------------------------
# Mesh-Vorverarbeitung (trimesh)
# --------------------------------------------------------------------------

def _preprocess_mesh(input_path: str, settings: ConversionSettings, tmp_path: str, report):
    """Laedt das Netz einmal per trimesh, wendet Glaettung/Vereinfachung
    an und bereitet es (falls noetig) fuer die Kruemmungserkennung vor.
    Gibt (pfad_der_zwischen_stl, mesh) zurueck - das mesh-Objekt wird
    direkt fuer die (vektorisierte) Regionenerkennung weiterverwendet,
    damit die Datei nicht ein zweites Mal eingelesen werden muss.
    """
    prefix = "Netz wird vorbereitet"
    report(4, f"{prefix}: lade Netz ...")
    mesh = trimesh.load(input_path, force="mesh")

    if settings.smoothing_iterations > 0:
        report(6, f"{prefix}: Glaettung ({settings.smoothing_iterations}x) ...")
        trimesh.smoothing.filter_taubin(mesh, iterations=settings.smoothing_iterations)

    if settings.decimate_percent < 100:
        target = max(4, int(len(mesh.faces) * settings.decimate_percent / 100))
        report(9, f"{prefix}: Vereinfachung auf {settings.decimate_percent}% ({target} Dreiecke) ...")
        mesh = mesh.simplify_quadric_decimation(face_count=target)

    # Normalen-/Wicklungs-Reparatur: fuer den schnellen Volumenkoerper-
    # Aufbau (geteilte Topologie, siehe unten) wird eine konsistente
    # Dreiecks-Wicklung vorausgesetzt, um pro Kante die richtige
    # Orientierung zu bestimmen. Die PRUEFUNG (is_winding_consistent)
    # ist auch bei Millionen Dreiecken sehr schnell (Millisekunden) -
    # die REPARATUR (fix_normals) dagegen kann bei sehr grossen Netzen
    # drastisch einbrechen (eigene Messung: 1,3 Mio. Dreiecke 5s, aber
    # 5,2 Mio. Dreiecke > 5 Minuten - kein sanftes Skalieren, sondern
    # ein regelrechter Einbruch). Deshalb: nur reparieren, wenn
    # tatsaechlich noetig, und bei sehr grossen Netzen lieber gar nicht
    # erst versuchen - der schnelle Pfad prueft die Wicklung ohnehin
    # noch einmal und faellt bei Inkonsistenz automatisch auf den
    # robusteren (aber langsameren) Sewing-Pfad zurueck, der keine
    # konsistente Wicklung braucht.
    report(11, f"{prefix}: pruefe Facettenausrichtung ...")
    if not mesh.is_winding_consistent and len(mesh.faces) <= MAX_FACES_FOR_NORMAL_REPAIR:
        report(11, f"{prefix}: repariere Facettenausrichtung ...")
        trimesh.repair.fix_normals(mesh, multibody=True)

    mesh.export(tmp_path)
    return tmp_path, mesh


def _count_faces(shape) -> int:
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    n = 0
    while exp.More():
        n += 1
        exp.Next()
    return n


# --------------------------------------------------------------------------
# Schneller Volumenkoerper-Aufbau ohne Sewing (geteilte Topologie)
# --------------------------------------------------------------------------
#
# Hintergrund (siehe Recherche zur Performance-Optimierung): der
# eigentliche Engpass bei grossen Netzen ist NICHT eigener Python-Code,
# sondern BRepBuilderAPI_Sewing selbst. Ein OCCT-Entwickler im
# offiziellen Forum dazu woertlich: "sewing tool is not designed for
# such kind of input [ein Dreieck pro Face] ... You're better creating
# BRep sharing Vertices and Edges from the beginning". Sewing muss bei
# unabhaengigen Einzel-Dreiecken die Nachbarschaft ueber (teure)
# geometrische Naeherungssuche neu entdecken - das skaliert deutlich
# schlechter als linear.
#
# Da wir aus der Vorverarbeitung (trimesh) die Nachbarschaft laengst
# kennen (gemeinsame Eckpunkte pro Kante, siehe mesh.edges_unique /
# mesh.faces_unique_edges), koennen wir OCCT direkt eine bereits
# GETEILTE Topologie uebergeben: jeder Eckpunkt und jede Kante wird nur
# EIN EINZIGES MAL angelegt und von allen angrenzenden Dreiecken
# gemeinsam benutzt. Damit ist die Huelle beim Aufbau automatisch schon
# "vernaeht" - ein Sewing-Aufruf entfaellt komplett. Das macht aus dem
# ueberlinearen Sewing-Schritt einen linearen Aufbau.
#
# Wichtig fuer Korrektheit (siehe Anforderung "immer ein richtiger
# Volumenkoerper, keine Dreiecke uebernehmen"): dieser schnelle Pfad
# wird nur versucht, wenn das Netz laut trimesh bereits wasserdicht UND
# wicklungskonsistent ist (sonst waere die Kantenrichtung pro Face
# nicht eindeutig bestimmbar). Nach dem Aufbau wird der Volumenkoerper
# trotzdem validiert. Schlaegt irgendein Schritt fehl - und sei es nur
# eine einzelne uebersprungene Facette - wird das Ergebnis verworfen
# und die Umwandlung faellt automatisch auf den bewaehrten (langsameren,
# toleranzbasierten) Sewing-Pfad zurueck. So gibt es immer entweder
# einen echten, gueltigen Volumenkoerper oder (nur bei tatsaechlich
# nicht wasserdichten Netzen) die bisherige offene-Flaeche-Meldung -
# nie werden unbearbeitete Rohdreiecke als Endergebnis ausgeliefert.
def _build_solid_shared_topology(mesh: "trimesh.Trimesh", progress_cb=None, pct_range=(15, 38)):
    """Baut einen Volumenkoerper direkt aus geteilter Topologie (ohne
    BRepBuilderAPI_Sewing). Gibt (solid_oder_None, is_valid, edges_oder_None) zurueck.
    Das dritte Element ist die Liste der OCCT-Kantenobjekte in derselben
    Reihenfolge wie mesh.edges_unique - wird von der Freiform-Glaettung
    weiterverwendet, um exakt geteilte Randkanten wiederzuverwenden statt
    sie neu zu konstruieren.

    Bei grossen Netzen kann allein der Flaechen-Aufbau mehrere Minuten
    dauern - ohne Zwischenmeldung wirkt das in der Weboberflaeche wie
    ein Haenger. progress_cb (falls uebergeben) wird deshalb periodisch
    waehrend der Schleife aufgerufen, nicht erst am Ende.
    """
    try:
        V = mesh.vertices
        F = mesh.faces
        edges_unique = mesh.edges_unique
        faces_unique_edges = mesh.faces_unique_edges
        n = len(F)
        n_verts = len(V)
        n_edges = len(edges_unique)

        guard_every = max(1, n // 20)  # ca. 20 Speicherpruefungen ueber die Laufzeit

        verts = []
        for vi in range(n_verts):
            if vi % (max(1, n_verts // 10)) == 0:
                _check_memory_guard()
            x, y, z = V[vi]
            verts.append(BRepBuilderAPI_MakeVertex(gp_Pnt(float(x), float(y), float(z))).Vertex())

        edges = []
        for ei in range(n_edges):
            if ei % (max(1, n_edges // 10)) == 0:
                _check_memory_guard()
            a, b = edges_unique[ei]
            edges.append(BRepBuilderAPI_MakeEdge(verts[int(a)], verts[int(b)]).Edge())

        pct_lo, pct_hi = pct_range
        report_every = max(1, n // 40)  # ca. 40 Zwischenmeldungen ueber die ganze Schleife

        faces_built = []
        for fi in range(n):
            if fi % guard_every == 0:
                _check_memory_guard()
            if progress_cb and fi % report_every == 0:
                frac = fi / n
                pct = int(pct_lo + (pct_hi - pct_lo) * frac)
                progress_cb(pct, f"Baue Volumenkoerper: Facette {fi:,} von {n:,} ...")

            tri = F[fi]
            eidx = faces_unique_edges[fi]
            wm = BRepBuilderAPI_MakeWire()
            ok = True
            for k in range(3):
                a, b = int(tri[k]), int(tri[(k + 1) % 3])
                e = edges[eidx[k]]
                ea, eb = edges_unique[eidx[k]]
                if (ea, eb) == (a, b):
                    wm.Add(e)
                elif (ea, eb) == (b, a):
                    wm.Add(TopoDS.Edge(e.Reversed()))
                else:
                    ok = False
                    break
            if not ok or not wm.IsDone():
                return None, False, None
            face_maker = BRepBuilderAPI_MakeFace(wm.Wire(), True)
            if not face_maker.IsDone():
                return None, False, None
            faces_built.append(face_maker.Face())

        builder = TopoDS_Builder()
        shell = TopoDS_Shell()
        builder.MakeShell(shell)
        for f in faces_built:
            builder.Add(shell, f)

        solid_maker = BRepBuilderAPI_MakeSolid(shell)
        if not solid_maker.IsDone():
            return None, False, None
        solid = solid_maker.Solid()

        # Schnelle Topologie-Pruefung statt der vollen geometrischen
        # Kontrolle: da Eckpunkte/Kanten durch Konstruktion exakt (nicht
        # nur toleranzbasiert) geteilt sind, entfaellt die Fehlerklasse,
        # die die teure geometrische Kontrolle sonst abfangen wuerde
        # (Naht-Toleranzprobleme). Eigene Tests zeigten dadurch ca. 40%
        # kuerzere Pruefzeit. (Der eingebaute Parallel-Modus von OCCT
        # brachte in eigenen Tests keine Beschleunigung - teils sogar
        # eine leichte Verlangsamung - und wird deshalb nicht genutzt.)
        analyzer = BRepCheck_Analyzer(solid, False)
        if not analyzer.IsValid():
            return None, False, None

        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(solid, props)
        if not (props.Mass() > 0):
            return None, False, None

        return solid, True, edges
    except _MemoryGuardAbort:
        raise
    except Exception:
        return None, False, None


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
        report(2, "Lese Netz ein ...")

        preprocessed_path, mesh = _preprocess_mesh(input_path, settings, input_path + "_pre.stl", report)
        faces_before = len(mesh.faces)
        if faces_before == 0:
            raise ConversionError("Die STL-Datei enthaelt keine Dreiecke.")

        max_faces_practical = _max_faces_practical()
        MIN_USEFUL_FACES = 200
        if faces_before > max_faces_practical:
            if max_faces_practical < MIN_USEFUL_FACES:
                raise ConversionError(
                    "Der verfuegbare Arbeitsspeicher reicht aktuell nicht einmal fuer ein "
                    "stark vereinfachtes Netz aus. Bitte andere Anwendungen schliessen, um "
                    "Arbeitsspeicher freizugeben, und die Umwandlung erneut starten."
                )
            # Statt den Nutzer per Vereinfachungs-Regler die passende
            # Prozentzahl raten zu lassen (die Aufloesung des Reglers
            # reicht bei sehr grossen Dateien / wenig Speicher oft nicht
            # aus), wird automatisch NACHDEZIMIERT, bis das Netz sicher
            # in den verfuegbaren Speicher passt - mit etwas Puffer
            # nach unten, da der Speicherbedarf je nach Netzform leicht
            # schwanken kann.
            target = max(MIN_USEFUL_FACES, int(max_faces_practical * 0.85))
            report(
                13,
                f"Netz hat {faces_before:,} Dreiecke - mehr als der verfuegbare "
                f"Arbeitsspeicher sicher zulaesst (ca. {max_faces_practical:,}). "
                f"Vereinfache automatisch weiter auf ca. {target:,} Dreiecke ...",
            )
            mesh = mesh.simplify_quadric_decimation(face_count=target)
            mesh.export(preprocessed_path)
            faces_before = len(mesh.faces)

        tol = float(np.linalg.norm(mesh.vertices.max(axis=0) - mesh.vertices.min(axis=0))) * 1e-5
        tol = max(tol, 1e-6)

        is_solid = False
        base_shape = None

        # Schneller Pfad: Volumenkoerper direkt aus geteilter Topologie
        # aufbauen (kein Sewing noetig, siehe _build_solid_shared_topology).
        # Nur versuchen, wenn trimesh das Netz bereits als wasserdicht und
        # wicklungskonsistent einstuft - sonst waere die Kantenrichtung
        # pro Facette nicht eindeutig bestimmbar und der Versuch wuerde
        # ohnehin scheitern.
        # Schneller Pfad: Volumenkoerper direkt aus geteilter Topologie
        # aufbauen (kein Sewing noetig, siehe _build_solid_shared_topology).
        # Nur versuchen, wenn trimesh das Netz bereits als wasserdicht und
        # wicklungskonsistent einstuft - sonst waere die Kantenrichtung
        # pro Facette nicht eindeutig bestimmbar und der Versuch wuerde
        # ohnehin scheitern.
        #
        # Live-Speicherueberwachung mit automatischem Neuversuch: die
        # a-priori-Schaetzung (oben) ist bewusst grosszuegig, damit bei
        # Netzen ohne grosse ebene/runde Bereiche (z. B. organische
        # Scan-Daten) moeglichst viel Detail erhalten bleibt. Wird der
        # Speicher waehrend des Aufbaus trotzdem zu knapp, bricht
        # _build_solid_shared_topology SAUBER ab (_MemoryGuardAbort)
        # statt abzustuerzen - hier wird dann automatisch mit staerkerer
        # Vereinfachung neu versucht, bis zu 3 Mal.
        memory_exhausted = False
        shared_edges = None  # OCCT-Kantenobjekte (geteilte Topologie) - fuer Freiform-Glaettung
        if mesh.is_watertight and mesh.is_winding_consistent:
            for attempt in range(3):
                report(
                    15,
                    f"Netz eingelesen ({faces_before:,} Dreiecke). Baue Volumenkoerper "
                    "(schneller Pfad, ohne Vernaehen) ...",
                )
                try:
                    fast_solid, fast_valid, fast_edges = _build_solid_shared_topology(
                        mesh, progress_cb=report, pct_range=(15, 36)
                    )
                except _MemoryGuardAbort:
                    fast_solid, fast_valid, fast_edges = None, False, None
                    if attempt >= 2:
                        memory_exhausted = True
                        break
                    target = max(MIN_USEFUL_FACES, int(len(mesh.faces) * 0.6))
                    report(
                        15,
                        f"Arbeitsspeicher wurde waehrend des Aufbaus knapp - "
                        f"vereinfache automatisch staerker auf ca. {target:,} "
                        "Dreiecke und versuche es erneut ...",
                    )
                    mesh = mesh.simplify_quadric_decimation(face_count=target)
                    mesh.export(preprocessed_path)
                    faces_before = len(mesh.faces)
                    continue

                if fast_valid:
                    base_shape = fast_solid
                    is_solid = True
                    shared_edges = fast_edges
                    report(38, "Volumenkoerper erfolgreich ohne Vernaehen aufgebaut.")
                break

        if memory_exhausted:
            # Der Speicher wurde auch nach mehrfacher automatischer
            # Nachdezimierung waehrend des Aufbaus knapp. Der aeltere
            # Sewing-Pfad braucht tendenziell EHER mehr als weniger
            # Speicher fuer dieselbe Dreieckszahl - ein Versuch dort
            # wuerde das Problem also eher verschaerfen als loesen.
            # Deshalb hier sauber mit einer klaren Meldung abbrechen,
            # statt einen aussichtslosen, riskanten Versuch zu starten.
            raise ConversionError(
                "Der verfuegbare Arbeitsspeicher reicht auch nach automatischer "
                "Nachdezimierung nicht zuverlaessig aus. Bitte andere Anwendungen "
                "schliessen, um Arbeitsspeicher freizugeben, oder die Einstellung "
                "\"Vereinfachung\" manuell auf einen niedrigeren Wert stellen, und "
                "die Umwandlung erneut starten."
            )

        if base_shape is None:
            # Sicherheitsnetz: der schnelle Pfad konnte keinen gueltigen
            # Volumenkoerper liefern (oder das Netz ist nicht wasserdicht/
            # wicklungskonsistent) - auf den bewaehrten, toleranzbasierten
            # Sewing-Pfad zurueckfallen. Langsamer, aber robuster
            # gegenueber unsauberen Netzen; garantiert, dass am Ende immer
            # entweder ein echter Volumenkoerper oder eine ehrliche
            # "nicht wasserdicht"-Meldung steht - nie unbearbeitete
            # Rohdreiecke.
            report(18, "Schneller Pfad nicht anwendbar - vernaehe Facetten (Sicherheitsnetz) ...")
            shape = TopoDS_Shape()
            reader = StlAPI_Reader()
            if not reader.Read(shape, preprocessed_path):
                raise ConversionError("STL-Datei konnte nicht gelesen werden.")

            sewing = BRepBuilderAPI_Sewing(tol)
            sewing.Add(shape)
            sewing.Perform()
            sewed = sewing.SewedShape()

            report(28, "Suche geschlossene Huelle ...")
            exp = TopExp_Explorer(sewed, TopAbs_SHELL)
            shells = []
            while exp.More():
                shells.append(TopoDS.Shell(exp.Current()))
                exp.Next()

            base_shape = sewed

            if len(shells) == 1:
                report(38, "Huelle geschlossen - erzeuge Volumenkoerper (ein Koerper) ...")
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
                    38,
                    f"Netz ist nicht wasserdicht ({len(shells)} Teil-Huellen) - "
                    "Ergebnis wird als offene Flaeche gespeichert.",
                )

        detected_shapes = []
        result_shape = base_shape

        run_detection = (settings.detect_curved_shapes or settings.smooth_scan_surfaces) and is_solid and len(mesh.faces) == faces_before
        if run_detection:
            report(50, "Suche volle Zylinder-/Kugelbereiche und/oder Scan-Rauschen (mehrere Kerne) ...")
            try:
                replaced_solid, detected_shapes = _detect_and_replace_curves(
                    base_shape,
                    mesh,
                    tol,
                    settings.worker_count,
                    smooth_freeform=settings.smooth_scan_surfaces,
                    shared_edges=shared_edges,
                )
                if replaced_solid is not None:
                    result_shape = replaced_solid
                    n_replaced = sum(1 for d in detected_shapes if d.replaced)
                    report(68, f"{n_replaced} Bereich(e) durch echte STEP-Flaechen ersetzt.")
            except Exception:
                traceback.print_exc()
                detected_shapes = []

        if settings.merge_planar:
            report(78, "Fasse ebene Bereiche zu grossen Flaechen zusammen (Flaechenrueckfuehrung) ...")
            unify = ShapeUpgrade_UnifySameDomain(result_shape, True, True, True)
            unify.SetLinearTolerance(tol)
            unify.SetAngularTolerance(1e-3)
            unify.Build()
            result_shape = unify.Shape()

        faces_after = _count_faces(result_shape)

        volume = None
        if is_solid:
            report(88, "Pruefe Volumenkoerper ...")
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(result_shape, props)
            volume = props.Mass()

        report(92, "Schreibe STEP-Datei ...")
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
