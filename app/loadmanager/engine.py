"""Lastmanagement-Engine: phasengenaue Verteilung des verfügbaren Stroms.

Die Engine ist bewusst als reine, zustandsfreie Funktion implementiert
(ohne Modbus/DB-Bezug), damit sie vollständig und deterministisch testbar
ist. Zustand (Hysterese, Haltezeiten) liegt in :mod:`app.loadmanager.smoothing`.

Harte Garantie: Die Summe der zugeteilten Ströme je Phase überschreitet
niemals die verfügbare Kapazität dieser Phase.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.models.base import DistributionStrategy

PHASES = ("L1", "L2", "L3")
_EPS = 1e-9


@dataclass(frozen=True)
class AllocStation:
    """Eingabe für die Zuteilung: eine ladebereite Station."""

    id: int
    phases: tuple[str, ...]  # belastete Phasen, z. B. ("L1",) oder PHASES
    min_current_a: float
    max_current_a: float
    priority: int = 0
    order: int = 0  # FIFO-Reihenfolge (Steckzeitpunkt); kleiner = früher


@dataclass(frozen=True)
class BoardNode:
    """Ein Knoten im Verteilungsbaum (Hauptverteilung oder Unterverteilung).

    Direkt angeschlossene Stationen (``stations``) und Kind-Verteiler
    (``children``) werden bei der Zuteilung wie Geschwister behandelt – ein
    Kind-Verteiler tritt dabei als virtuelle 3-phasige Station auf (siehe
    :func:`allocate_tree`), begrenzt durch seine eigene ``fuse_a``.

    ``strategy``: Verteilstrategie für diesen Knoten. ``None`` = erbt vom
    übergeordneten Verteiler (kaskadierend) bzw. zuletzt von der an
    :func:`allocate_tree` übergebenen Standard-Strategie.
    """

    id: int
    fuse_a: float
    priority: int = 0
    strategy: DistributionStrategy | None = None
    children: tuple["BoardNode", ...] = ()
    stations: tuple[AllocStation, ...] = ()


def _allocate_greedy(
    stations: list[AllocStation],
    capacity: dict[str, float],
) -> dict[int, float]:
    """Greedy-Zuteilung in gegebener Reihenfolge (für priority/fifo).

    Jede Station erhält so viel wie möglich (bis max_current), begrenzt durch
    die kleinste freie Kapazität ihrer Phasen.
    """
    remaining = dict(capacity)
    targets: dict[int, float] = {}
    for s in stations:
        headroom = min(remaining[p] for p in s.phases)
        give = min(s.max_current_a, max(0.0, headroom))
        targets[s.id] = give
        for p in s.phases:
            remaining[p] -= give
    return targets


def _allocate_equal(
    stations: list[AllocStation],
    capacity: dict[str, float],
) -> dict[int, float]:
    """Gleichmäßige (max-min-faire) Zuteilung per Water-Filling.

    Alle Stationen wachsen gemeinsam, bis eine Phase gesättigt ist oder eine
    Station ihr Maximum erreicht. Gesättigte/maximale Stationen werden
    eingefroren, der Rest wächst weiter.
    """
    targets: dict[int, float] = {s.id: 0.0 for s in stations}
    remaining = dict(capacity)
    frozen: set[int] = set()

    # Obergrenze der Iterationen als Sicherheitsnetz gegen Endlosschleifen
    for _ in range(len(stations) * len(PHASES) + 2):
        movable = [
            s for s in stations
            if s.id not in frozen and targets[s.id] < s.max_current_a - _EPS
        ]
        if not movable:
            break

        # Maximales gleichmäßiges Inkrement bestimmen
        candidate_deltas: list[float] = []
        for phase in capacity:
            loaders = [s for s in movable if phase in s.phases]
            if loaders:
                candidate_deltas.append(remaining[phase] / len(loaders))
        for s in movable:
            candidate_deltas.append(s.max_current_a - targets[s.id])

        delta = min(candidate_deltas) if candidate_deltas else 0.0

        if delta <= _EPS:
            # Eine Phase ist gesättigt: alle darauf ladenden Stationen einfrieren
            progressed = False
            for phase in capacity:
                if remaining[phase] <= _EPS:
                    for s in movable:
                        if phase in s.phases and s.id not in frozen:
                            frozen.add(s.id)
                            progressed = True
            if not progressed:
                break
            continue

        for s in movable:
            targets[s.id] += delta
        for phase in capacity:
            loaders = [s for s in movable if phase in s.phases]
            remaining[phase] -= delta * len(loaders)

        for s in movable:
            if targets[s.id] >= s.max_current_a - _EPS:
                frozen.add(s.id)

    return targets


def allocate(
    stations: list[AllocStation],
    capacity: dict[str, float],
    strategy: DistributionStrategy,
    floor_amps: bool = True,
) -> dict[int, float]:
    """Verteilt den verfügbaren Strom auf die aktiven Stationen.

    :param capacity: verfügbarer Strom je Phase (L1/L2/L3).
    :param strategy: equal / priority / fifo.
    :param floor_amps: Sollwerte auf ganze Ampere abrunden (konservativ, damit
                       die Summe die Grenze garantiert nicht überschreitet).
    :returns: ``station_id -> Sollstrom (A)``. Stationen unterhalb ihres
              Minimalstroms werden auf 0 A pausiert; die dadurch frei werdende
              Kapazität wird an die übrigen Stationen weiterverteilt.
    """
    capacity = {p: max(0.0, capacity.get(p, 0.0)) for p in PHASES}

    def order_key(s: AllocStation):
        if strategy is DistributionStrategy.PRIORITY:
            return (-s.priority, s.order, s.id)
        return (s.order, s.id)  # FIFO

    active = list(stations)
    # Iterativ: nach jeder Zuteilung Stationen < min_current pausieren und
    # die frei gewordene Kapazität neu verteilen, bis stabil.
    while True:
        if not active:
            return {s.id: 0.0 for s in stations}

        if strategy is DistributionStrategy.EQUAL:
            targets = _allocate_equal(active, capacity)
        else:
            ordered = sorted(active, key=order_key)
            targets = _allocate_greedy(ordered, capacity)

        # Stationen unter Minimalstrom identifizieren
        below = [s for s in active if targets[s.id] < s.min_current_a - _EPS]
        if not below:
            break
        # Die "teuerste" (kleinste Zuteilung) pausieren und erneut verteilen
        below.sort(key=lambda s: targets[s.id])
        drop = below[0]
        active = [s for s in active if s.id != drop.id]

    result: dict[int, float] = {s.id: 0.0 for s in stations}
    for s in active:
        value = targets[s.id]
        if floor_amps:
            value = math.floor(value + _EPS)
        # Nach dem Abrunden erneut gegen die Grenzen prüfen
        if value < s.min_current_a - _EPS:
            value = 0.0
        else:
            value = min(value, s.max_current_a)
        result[s.id] = float(value)
    return result


def allocate_tree(
    root: BoardNode,
    root_capacity: dict[str, float],
    default_strategy: DistributionStrategy,
    floor_amps: bool = True,
) -> dict[int, float]:
    """Verteilt rekursiv top-down über die Verteilungshierarchie.

    An jedem Knoten werden die direkt angeschlossenen Stationen und die
    Kind-Verteiler gemeinsam per :func:`allocate` verteilt – ein
    Kind-Verteiler tritt dabei als virtuelle 3-phasige Station auf
    (``max_current_a = fuse_a``, ``min_current_a = 0`` – ein Verteiler
    pausiert nie, er bekommt einfach so viel wie zugeteilt, auch 0 A). Was
    ihm zugeteilt wird, reicht er anschließend als eigene Kapazität an
    seinen Teilbaum weiter.

    Verteilstrategie kaskadiert: ``node.strategy`` überschreibt für den
    Knoten UND seinen gesamten Teilbaum die von seinem Elternteil geerbte
    Strategie (``default_strategy`` gilt an der Wurzel, falls diese kein
    eigenes ``strategy`` gesetzt hat).

    Ohne Unterverteiler (nur Wurzel mit direkt angeschlossenen Stationen)
    reduziert sich das auf einen einzigen ``allocate()``-Aufruf – identisches
    Verhalten wie vorher.

    Virtuelle Stations-IDs für Verteiler-Zweige (``-board.id``) liegen in
    einem zu echten Stations-IDs disjunkten Namensraum (Stations-IDs sind
    stets positive Datenbank-Primärschlüssel).
    """
    result: dict[int, float] = {}

    def recurse(node: BoardNode, capacity: dict[str, float], inherited_strategy: DistributionStrategy) -> None:
        effective_strategy = node.strategy or inherited_strategy
        virtual_children = [
            AllocStation(
                id=-child.id,
                phases=PHASES,
                min_current_a=0.0,
                max_current_a=child.fuse_a,
                priority=child.priority,
            )
            for child in node.children
        ]
        combined = list(node.stations) + virtual_children
        targets = allocate(combined, capacity, effective_strategy, floor_amps=floor_amps)

        for station in node.stations:
            result[station.id] = targets[station.id]
        for child in node.children:
            given = targets[-child.id]
            recurse(child, {p: given for p in PHASES}, effective_strategy)

    recurse(root, root_capacity, default_strategy)
    return result


def subtree_totals(node: BoardNode, assignment: dict[int, float]) -> dict[str, float]:
    """Summiert die zugeteilten Ströme je Phase über den GESAMTEN Teilbaum
    eines Knotens (eigene Stationen + rekursiv alle Kind-Verteiler).

    Dient sowohl der Verifikation (Grenzwert-Garantie an jedem Knoten) als
    auch der Berechnung der Rest-Absicherung für einen zweiten,
    nachrangigen Verteilungslauf (siehe :func:`remaining_capacity_tree`).
    """
    totals = {p: 0.0 for p in PHASES}
    for st in node.stations:
        v = assignment.get(st.id, 0.0)
        for p in st.phases:
            totals[p] += v
    for child in node.children:
        child_totals = subtree_totals(child, assignment)
        for p in PHASES:
            totals[p] += child_totals[p]
    return totals


def remaining_capacity_tree(
    node: BoardNode,
    used: dict[int, float],
    stations_by_board: dict[int, tuple[AllocStation, ...]],
) -> BoardNode:
    """Baut einen Baum für einen zweiten, NACHRANGIGEN Verteilungslauf (z. B.
    PV-Überschussladen): Jede Absicherung wird um die im ersten Lauf
    (``used``, dessen Zuteilungsergebnis) bereits verbrauchte Kapazität
    reduziert, und die Stationen jedes Knotens werden durch
    ``stations_by_board`` ersetzt (die nachrangige Ladepunkt-Gruppe für
    diesen Verteiler, z. B. PV-Only-Ladepunkte).

    Die Absicherung ist ein einzelner Wert je Knoten (3-phasig symmetrisch,
    siehe :class:`BoardNode`) – als "verbraucht" gilt konservativ die
    höchstbelastete Phase des ersten Laufs.
    """
    children = tuple(
        remaining_capacity_tree(c, used, stations_by_board) for c in node.children
    )
    consumed = subtree_totals(node, used)
    used_scalar = max(consumed.values()) if consumed else 0.0
    remaining_fuse = max(0.0, node.fuse_a - used_scalar)
    return BoardNode(
        id=node.id,
        fuse_a=remaining_fuse,
        priority=node.priority,
        strategy=node.strategy,
        children=children,
        stations=stations_by_board.get(node.id, ()),
    )


def phase_totals(assignment: dict[int, float], stations: list[AllocStation]) -> dict[str, float]:
    """Summiert die zugeteilten Ströme je Phase (zur Verifikation/Anzeige)."""
    by_id = {s.id: s for s in stations}
    totals = {p: 0.0 for p in PHASES}
    for sid, current in assignment.items():
        st = by_id.get(sid)
        if not st:
            continue
        for phase in st.phases:
            totals[phase] += current
    return totals
