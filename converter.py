"""
STL -> STEP Konverter ("Flaechenrueckfuehrung light")

Vorgehen (angelehnt an das Grundprinzip von Geomagic, aber deutlich
einfacher als eine vollstaendige NURBS-Flaechenrueckfuehrung):

1. Das komplette Dreiecksnetz der STL-Datei wird eingelesen.
2. Alle Dreiecke werden ueber BRepBuilderAPI_Sewing zu einer
   zusammenhaengenden Huelle (Shell) vernaeht.
3. Ist die Huelle geschlossen (wasserdicht), wird daraus EIN
   Volumenkoerper (Solid) gebaut - das gesamte Gitternetz wird also
   zu einem einzigen Koerper.
4. Mit ShapeUpgrade_UnifySameDomain werden alle benachbarten,
   in der gleichen Ebene liegenden Dreiecksflaechen zu jeweils EINER
   grossen, echten (analytischen) Planarflaeche zusammengefasst.
   Ebene Bereiche des Modells bestehen danach also nicht mehr aus
   hunderten Mini-Dreiecken, sondern aus sauberen Flaechen - so wie
   es auch ein CAD-Flaechenrueckfuehrungswerkzeug fuer ebene Bereiche
   tun wuerde.
5. Das Ergebnis wird als STEP (AP214) Datei geschrieben.

Wichtige Einschraenkung (bitte im README/UI kommunizieren):
Gekruemmte / freiformige Bereiche (Rundungen, organische Formen)
werden von Schritt 4 NICHT automatisch in glatte NURBS-Flaechen
umgewandelt, da eine vollwertige, robuste automatische
Segmentierung + Flaechenanpassung (wie in Geomagic Wrap/Design X)
ein eigenstaendiges, sehr aufwaendiges Forschungsthema ist. Diese
Bereiche bleiben als (ggf. bereits etwas vereinfachte) Dreiecks-
Facetten erhalten, sind aber weiterhin Teil des EINEN Volumenkoerpers
und liegen als gueltige STEP-Flaechen vor. Eine echte
Freiform-Flaechenanpassung ist als moegliche spaetere Ausbaustufe
denkbar (siehe CHANGELOG "Ideen fuer spaeter").
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass
from typing import Callable, Optional

from OCP.StlAPI import StlAPI_Reader
from OCP.TopoDS import TopoDS_Shape, TopoDS
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.IFSelect import IFSelect_RetDone
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_SHELL, TopAbs_FACE
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib

ProgressCallback = Callable[[int, str], None]


@dataclass
class ConversionResult:
    output_path: str
    is_solid: bool
    face_count_before: int
    face_count_after: int
    volume: Optional[float]


class ConversionError(Exception):
    pass


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


def convert_stl_to_step(
    input_path: str,
    output_path: str,
    progress_cb: Optional[ProgressCallback] = None,
) -> ConversionResult:
    """Wandelt eine STL-Datei per Flaechenrueckfuehrung in eine STEP-Datei um."""

    def report(pct: int, msg: str) -> None:
        if progress_cb:
            progress_cb(pct, msg)

    try:
        report(3, "Lese STL-Datei ein ...")
        shape = TopoDS_Shape()
        reader = StlAPI_Reader()
        if not reader.Read(shape, input_path):
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

        report(40, "Suche geschlossene Huelle ...")
        exp = TopExp_Explorer(sewed, TopAbs_SHELL)
        shells = []
        while exp.More():
            shells.append(TopoDS.Shell(exp.Current()))
            exp.Next()

        is_solid = False
        base_shape = sewed

        if len(shells) == 1:
            report(55, "Huelle geschlossen - erzeuge Volumenkoerper (ein Koerper) ...")
            try:
                maker = BRepBuilderAPI_MakeSolid(shells[0])
                if maker.IsDone():
                    solid = maker.Solid()
                    analyzer = BRepCheck_Analyzer(solid)
                    if analyzer.IsValid():
                        base_shape = solid
                        is_solid = True
            except Exception:
                # Kein gueltiger Volumenkoerper moeglich -> als offene
                # Flaeche (Shell) weiterverarbeiten, nicht abbrechen.
                pass
        else:
            report(
                55,
                f"Netz ist nicht wasserdicht ({len(shells)} Teil-Huellen) - "
                "Ergebnis wird als offene Flaeche gespeichert.",
            )

        report(70, "Fasse ebene Bereiche zu grossen Flaechen zusammen (Flaechenrueckfuehrung) ...")
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

        report(93, "Schreibe STEP-Datei ...")
        writer = STEPControl_Writer()
        writer.Transfer(result_shape, STEPControl_AsIs)
        status = writer.Write(output_path)
        if status != IFSelect_RetDone:
            raise ConversionError("STEP-Datei konnte nicht geschrieben werden.")

        report(100, "Fertig.")
        return ConversionResult(
            output_path=output_path,
            is_solid=is_solid,
            face_count_before=faces_before,
            face_count_after=faces_after,
            volume=volume,
        )
    except ConversionError:
        raise
    except Exception as exc:  # unerwarteter Fehler -> sauber melden statt Crash
        traceback.print_exc()
        raise ConversionError(f"Unerwarteter Fehler bei der Umwandlung: {exc}") from exc
