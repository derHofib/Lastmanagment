"""Tests der Lastmanagement-Engine.

Zentrale Zusicherung: Die Phasensummen überschreiten die verfügbare
Kapazität nie, und die Minimalstrom-Regel greift korrekt.
"""

import pytest

from app.loadmanager.engine import (
    AllocStation,
    BoardNode,
    PHASES,
    allocate,
    allocate_tree,
    phase_totals,
    remaining_capacity_tree,
    subtree_totals,
)
from app.loadmanager.smoothing import SetpointSmoother
from app.models.base import DistributionStrategy


def cap(l1=100.0, l2=100.0, l3=100.0):
    return {"L1": l1, "L2": l2, "L3": l3}


def s(id, phases=PHASES, mn=6.0, mx=32.0, prio=0, order=0):
    return AllocStation(id=id, phases=tuple(phases), min_current_a=mn,
                        max_current_a=mx, priority=prio, order=order)


def assert_within_limits(assignment, stations, capacity):
    totals = phase_totals(assignment, stations)
    for p in PHASES:
        assert totals[p] <= capacity[p] + 1e-6, (
            f"Phase {p} überschritten: {totals[p]} > {capacity[p]}"
        )


# --- Grundlegende Verteilung ----------------------------------------------

def test_equal_split_three_phase():
    stations = [s(1), s(2)]
    capacity = cap(32, 32, 32)
    res = allocate(stations, capacity, DistributionStrategy.EQUAL)
    # 32 A auf zwei 3p-Stationen -> je 16 A
    assert res[1] == 16
    assert res[2] == 16
    assert_within_limits(res, stations, capacity)


def test_equal_respects_station_max():
    stations = [s(1, mx=10), s(2, mx=32)]
    capacity = cap(40, 40, 40)
    res = allocate(stations, capacity, DistributionStrategy.EQUAL)
    assert res[1] == 10  # gedeckelt durch Stationsmaximum
    assert res[2] >= 10
    assert_within_limits(res, stations, capacity)


def test_single_station_gets_max_not_more():
    stations = [s(1, mx=32)]
    res = allocate(stations, cap(63, 63, 63), DistributionStrategy.EQUAL)
    assert res[1] == 32


# --- Minimalstrom-Regel ----------------------------------------------------

def test_min_current_pauses_station():
    # 10 A auf zwei 3p-Stationen -> je 5 A < 6 A min -> eine wird pausiert,
    # die andere bekommt die volle Kapazität.
    stations = [s(1, mn=6), s(2, mn=6)]
    capacity = cap(10, 10, 10)
    res = allocate(stations, capacity, DistributionStrategy.EQUAL)
    active = [sid for sid, v in res.items() if v > 0]
    assert len(active) == 1
    assert res[active[0]] == 10
    assert_within_limits(res, stations, capacity)


def test_all_paused_when_below_min():
    stations = [s(1, mn=6)]
    res = allocate(stations, cap(5, 5, 5), DistributionStrategy.EQUAL)
    assert res[1] == 0


# --- Priorität -------------------------------------------------------------

def test_priority_high_first():
    stations = [s(1, prio=1, mx=32), s(2, prio=10, mx=32)]
    capacity = cap(32, 32, 32)
    res = allocate(stations, capacity, DistributionStrategy.PRIORITY)
    # Station 2 (höhere Prio) bekommt ihr Maximum zuerst
    assert res[2] == 32
    assert res[1] == 0
    assert_within_limits(res, stations, capacity)


def test_priority_leftover_to_next():
    stations = [s(1, prio=10, mx=20), s(2, prio=5, mx=32)]
    capacity = cap(32, 32, 32)
    res = allocate(stations, capacity, DistributionStrategy.PRIORITY)
    assert res[1] == 20
    assert res[2] == 12
    assert_within_limits(res, stations, capacity)


def test_priority_same_tier_splits_fairly_not_by_order():
    # Zwei Stationen mit GLEICHER Priorität, aber unterschiedlicher
    # Steckreihenfolge (order): die verbleibende Kapazität wird fair
    # aufgeteilt (Water-Filling), nicht strikt nach Anschlussreihenfolge.
    stations = [s(1, prio=5, mx=32, order=1), s(2, prio=5, mx=32, order=2)]
    capacity = cap(40, 40, 40)
    res = allocate(stations, capacity, DistributionStrategy.PRIORITY)
    assert res[1] == 20
    assert res[2] == 20
    assert_within_limits(res, stations, capacity)


def test_priority_cascade_with_shared_tier():
    # Höchste Priorität zuerst (voll), danach teilen sich zwei Stationen mit
    # gleicher (niedrigerer) Priorität den Rest fair untereinander.
    stations = [
        s(1, prio=10, mx=16),
        s(2, prio=3, mx=32, order=1),
        s(3, prio=3, mx=32, order=5),
    ]
    capacity = cap(32, 32, 32)
    res = allocate(stations, capacity, DistributionStrategy.PRIORITY)
    assert res[1] == 16
    # Rest: 32 - 16 = 16 A, fair auf 2 und 3 aufgeteilt -> je 8 A
    assert res[2] == 8
    assert res[3] == 8
    assert_within_limits(res, stations, capacity)


# --- FIFO ------------------------------------------------------------------

def test_fifo_order():
    stations = [s(1, order=2, mx=32), s(2, order=1, mx=32)]
    capacity = cap(32, 32, 32)
    res = allocate(stations, capacity, DistributionStrategy.FIFO)
    # Station 2 hat zuerst gesteckt -> bekommt zuerst
    assert res[2] == 32
    assert res[1] == 0


# --- Phasengenaue Betrachtung ---------------------------------------------

def test_single_phase_stations_independent_phases():
    # Zwei 1p-Stationen auf unterschiedlichen Phasen teilen sich nicht
    stations = [s(1, phases=("L1",), mx=32), s(2, phases=("L2",), mx=32)]
    capacity = cap(16, 16, 16)
    res = allocate(stations, capacity, DistributionStrategy.EQUAL)
    assert res[1] == 16
    assert res[2] == 16
    assert_within_limits(res, stations, capacity)


def test_mixed_phase_limit_binding():
    # Zwei Stationen auf L1, eine auf L2. L1 ist die bindende Phase.
    stations = [
        s(1, phases=("L1",), mx=32),
        s(2, phases=("L1",), mx=32),
        s(3, phases=("L2",), mx=32),
    ]
    capacity = cap(20, 32, 32)
    res = allocate(stations, capacity, DistributionStrategy.EQUAL)
    # L1 teilt 20 A auf zwei Stationen -> je 10 A
    assert res[1] == 10
    assert res[2] == 10
    assert res[3] == 32
    assert_within_limits(res, stations, capacity)


def test_three_phase_and_single_phase_shared():
    # Eine 3p- und eine 1p-Station (L1) konkurrieren nur auf L1
    stations = [s(1, phases=PHASES, mx=32), s(2, phases=("L1",), mx=32)]
    capacity = cap(30, 30, 30)
    res = allocate(stations, capacity, DistributionStrategy.EQUAL)
    # Auf L1 teilen sich beide 30 A -> je 15; L2/L3 hat nur Station 1
    assert res[1] == 15
    assert res[2] == 15
    assert_within_limits(res, stations, capacity)


# --- Grenzwert-Garantie unter Zufallslasten -------------------------------

@pytest.mark.parametrize("strategy", list(DistributionStrategy))
@pytest.mark.parametrize("limit", [16, 20, 32, 63])
def test_never_exceeds_limit_many_stations(strategy, limit):
    import random
    rng = random.Random(42)
    phase_opts = [("L1",), ("L2",), ("L3",), PHASES]
    stations = [
        s(i, phases=rng.choice(phase_opts), mn=6,
          mx=rng.choice([16, 22, 32]), prio=rng.randint(0, 5), order=i)
        for i in range(1, 12)
    ]
    capacity = cap(limit, limit, limit)
    res = allocate(stations, capacity, strategy)
    assert_within_limits(res, stations, capacity)
    # Jede aktive Station respektiert ihr Minimum
    for st in stations:
        assert res[st.id] == 0 or res[st.id] >= st.min_current_a


def test_no_stations():
    assert allocate([], cap(), DistributionStrategy.EQUAL) == {}


# --- Hysterese / Glättung --------------------------------------------------

def test_smoother_first_value_passes():
    t = [0.0]
    sm = SetpointSmoother(min_change_a=1.0, min_hold_s=30.0, time_fn=lambda: t[0])
    assert sm.desired(1, 16.0) == 16.0


def test_smoother_small_increase_suppressed():
    t = [0.0]
    sm = SetpointSmoother(min_change_a=2.0, min_hold_s=0.0, time_fn=lambda: t[0])
    sm.desired(1, 16.0)
    # +1 A < min_change 2 A -> unterdrückt
    assert sm.desired(1, 17.0) == 16.0


def test_smoother_hold_time_blocks_increase():
    t = [0.0]
    sm = SetpointSmoother(min_change_a=1.0, min_hold_s=30.0, time_fn=lambda: t[0])
    sm.desired(1, 10.0)
    t[0] = 5.0
    # Erhöhung vor Ablauf der Haltezeit -> blockiert
    assert sm.desired(1, 20.0) == 10.0
    t[0] = 40.0
    assert sm.desired(1, 20.0) == 20.0


def test_smoother_reduction_always_immediate():
    t = [0.0]
    sm = SetpointSmoother(min_change_a=1.0, min_hold_s=30.0, time_fn=lambda: t[0])
    sm.desired(1, 20.0)
    # Reduzierung geht sofort, auch innerhalb der Haltezeit
    assert sm.desired(1, 8.0) == 8.0


def test_smoother_pause_always_immediate():
    t = [0.0]
    sm = SetpointSmoother(min_change_a=5.0, min_hold_s=30.0, time_fn=lambda: t[0])
    sm.desired(1, 16.0)
    # Pausieren (0 A) muss trotz min_change durchkommen
    assert sm.desired(1, 0.0) == 0.0


# --- Verteilungshierarchie (Hauptverteilung / Unterverteilung) ------------

def assert_tree_within_limits(node: BoardNode, assignment: dict) -> None:
    """Prüft rekursiv, dass an JEDEM Knoten des Baums dessen Absicherung
    (fuse_a) je Phase eingehalten wird – die zentrale Garantie von
    allocate_tree()."""
    totals = subtree_totals(node, assignment)
    for p in PHASES:
        assert totals[p] <= node.fuse_a + 1e-6, (
            f"Verteiler {node.id}: Phase {p} überschritten "
            f"({totals[p]} > {node.fuse_a})"
        )
    for child in node.children:
        assert_tree_within_limits(child, assignment)


def test_allocate_tree_without_subboards_matches_flat_allocate():
    # Ohne Unterverteiler muss allocate_tree() exakt allocate() entsprechen
    # (keine Regression für bestehende, einfache Installationen).
    stations = (s(1), s(2), s(3, mx=16))
    root = BoardNode(id=1, fuse_a=63, stations=stations)
    capacity = cap(63, 63, 63)

    tree_result = allocate_tree(root, capacity, DistributionStrategy.EQUAL)
    flat_result = allocate(list(stations), capacity, DistributionStrategy.EQUAL)

    assert tree_result == flat_result
    assert_tree_within_limits(root, tree_result)


def test_allocate_tree_middle_level_bottleneck():
    # Zwei Stationen (je max 32 A) hängen an einer Unterverteilung mit nur
    # 20 A Absicherung -> trotz reichlich Kapazität an Wurzel (100 A) und an
    # den Stationen selbst (32 A) begrenzt die Unterverteilung auf 20 A/2.
    leaf_a = s(10, mx=32)
    leaf_b = s(11, mx=32)
    sub = BoardNode(id=2, fuse_a=20, stations=(leaf_a, leaf_b))
    sibling_station = s(20, mx=32)
    root = BoardNode(id=1, fuse_a=100, children=(sub,), stations=(sibling_station,))

    result = allocate_tree(root, cap(100, 100, 100), DistributionStrategy.EQUAL)

    assert result[10] == 10
    assert result[11] == 10
    assert result[20] == 32  # nicht durch die Unterverteilung begrenzt
    assert_tree_within_limits(root, result)


def test_allocate_tree_mixed_single_and_three_phase_under_subboard():
    # Zwei 1-phasige Stationen (beide L1) + eine 3-phasige Station teilen
    # sich eine Unterverteilung mit 20 A Absicherung.
    single_a = s(30, phases=("L1",), mx=16)
    single_b = s(31, phases=("L1",), mx=16)
    three_phase = s(32, phases=PHASES, mx=16)
    sub = BoardNode(id=5, fuse_a=20, stations=(single_a, single_b, three_phase))
    root = BoardNode(id=1, fuse_a=100, children=(sub,))

    result = allocate_tree(root, cap(100, 100, 100), DistributionStrategy.EQUAL)

    # Symmetrie: beide identischen 1-phasigen Stationen bekommen denselben Wert
    assert result[30] == result[31]
    # Die 3-phasige Station teilt sich L1 mit den beiden anderen und wird
    # dadurch auf denselben Wert eingefroren (ein Sollwert gilt für alle
    # ihre Phasen gleichermaßen).
    assert result[32] == result[30]
    assert_tree_within_limits(root, result)


@pytest.mark.parametrize("strategy", list(DistributionStrategy))
def test_allocate_tree_random_never_exceeds_any_node(strategy):
    import random
    rng = random.Random(7)
    phase_opts = [("L1",), ("L2",), ("L3",), PHASES]

    def random_stations(ids, order_offset):
        return tuple(
            s(i, phases=rng.choice(phase_opts), mn=6,
              mx=rng.choice([16, 22, 32]), prio=rng.randint(0, 5),
              order=order_offset + i)
            for i in ids
        )

    leaf_a = BoardNode(id=101, fuse_a=rng.choice([16, 25, 32]),
                       priority=rng.randint(0, 3),
                       stations=random_stations(range(1, 4), 0))
    leaf_b = BoardNode(id=102, fuse_a=rng.choice([16, 25, 32]),
                       priority=rng.randint(0, 3),
                       stations=random_stations(range(4, 7), 10))
    mid = BoardNode(id=10, fuse_a=rng.choice([20, 32, 40]),
                    priority=rng.randint(0, 3),
                    children=(leaf_a, leaf_b),
                    stations=random_stations(range(7, 9), 20))
    root = BoardNode(id=1, fuse_a=rng.choice([32, 50, 63]),
                     children=(mid,), stations=random_stations(range(9, 11), 30))

    result = allocate_tree(root, cap(63, 63, 63), strategy)

    assert_tree_within_limits(root, result)
    # Minimalstrom-Regel gilt auch innerhalb des Baums
    all_stations = {
        st.id: st
        for node in (leaf_a, leaf_b, mid, root)
        for st in node.stations
    }
    for sid, st in all_stations.items():
        assert result[sid] == 0 or result[sid] >= st.min_current_a


# --- Strategie pro Verteiler (Kaskadierung) --------------------------------

def test_allocate_tree_board_priority_cascades_then_station_priority_inside():
    # Zwei Unterverteilungen mit unterschiedlicher Priorität hängen an der
    # Hauptverteilung: die mit der höheren Prioritätszahl bekommt zuerst
    # Kapazität bis zu ihrer eigenen Absicherung, der Rest geht an die
    # niedriger priorisierte. INNERHALB jeder Unterverteilung teilen sich
    # ihre Ladepunkte die ihr zugeteilte Kapazität wiederum nach ihrer
    # eigenen Priorität (hier: gleiche Priorität -> fair geteilt).
    high_a = s(10, prio=5, mx=32)
    high_b = s(11, prio=5, mx=32)
    high_board = BoardNode(id=2, fuse_a=25, priority=10,
                            strategy=DistributionStrategy.PRIORITY,
                            stations=(high_a, high_b))

    low_a = s(20, prio=1, mx=32)
    low_board = BoardNode(id=3, fuse_a=25, priority=1,
                           strategy=DistributionStrategy.PRIORITY,
                           stations=(low_a,))

    root = BoardNode(id=1, fuse_a=32, children=(high_board, low_board))
    result = allocate_tree(root, cap(32, 32, 32), DistributionStrategy.PRIORITY)

    # high_board (Prio 10) bekommt zuerst volle Kapazität bis zu seiner
    # eigenen Absicherung (25 A) -> die beiden gleich priorisierten
    # Ladepunkte darunter teilen sich diese 25 A fair (je 12.5 A, konservativ
    # auf ganze Ampere abgerundet -> je 12 A).
    assert result[10] == 12
    assert result[11] == 12
    # low_board (Prio 1) bekommt nur den Rest der Wurzel-Kapazität (32-25=7 A)
    assert result[20] == 7
    assert_tree_within_limits(root, result)


def test_allocate_tree_board_strategy_override_changes_split():
    # Unterverteilung überschreibt die globale EQUAL-Strategie auf PRIORITY
    a = s(10, prio=10, mx=32, mn=6)
    c = s(11, prio=1, mx=32, mn=6)
    b = BoardNode(id=2, fuse_a=40, strategy=DistributionStrategy.PRIORITY, stations=(a, c))
    root = BoardNode(id=1, fuse_a=100, children=(b,))

    result = allocate_tree(root, cap(100, 100, 100), DistributionStrategy.EQUAL)

    # Unter PRIORITY: höhere Priorität wird zuerst voll bedient, Rest an C
    assert result[10] == 32
    assert result[11] == 8
    assert_tree_within_limits(root, result)


def test_allocate_tree_board_strategy_inherits_to_grandchildren():
    # Ein Unterverteiler OHNE eigene Strategie erbt von seinem Elternteil
    # (nicht direkt von der globalen Default-Strategie).
    leaf_a = s(20, prio=10, mx=16, mn=6)
    leaf_c = s(21, prio=1, mx=16, mn=6)
    leaf_board = BoardNode(id=3, fuse_a=30, stations=(leaf_a, leaf_c))
    mid = BoardNode(id=2, fuse_a=30, strategy=DistributionStrategy.PRIORITY, children=(leaf_board,))
    root = BoardNode(id=1, fuse_a=63, children=(mid,))

    result = allocate_tree(root, cap(63, 63, 63), DistributionStrategy.EQUAL)

    # leaf_board erbt PRIORITY von mid, nicht das globale EQUAL
    assert result[20] == 16
    assert result[21] == 14
    assert_tree_within_limits(root, result)


# --- PV-Überschussladen: zweiter, nachrangiger Verteilungslauf ------------

def test_remaining_capacity_tree_basic():
    grid_station = s(1, mx=32, mn=6)
    root = BoardNode(id=1, fuse_a=63, stations=(grid_station,))
    pass1 = allocate_tree(root, cap(63, 63, 63), DistributionStrategy.EQUAL)
    assert pass1[1] == 32

    pv_station = s(2, mx=16, mn=6)
    pv_root = remaining_capacity_tree(root, pass1, {1: (pv_station,)})
    assert pv_root.fuse_a == pytest.approx(63 - 32)
    assert pv_root.stations == (pv_station,)


def test_pv_two_pass_capped_by_remaining_fuse_not_raw_surplus():
    # Unterverteilung mit 20 A Absicherung: eine Grid-Vorrang-Station zieht
    # 14 A. Ein angenommener PV-Überschuss von 30 A an der Wurzel darf die
    # PV-Only-Station NICHT voll bekommen, da die Unterverteilung selbst nur
    # noch 20-14=6 A frei hat.
    grid = s(1, mx=14, mn=6)
    sub = BoardNode(id=2, fuse_a=20, stations=(grid,))
    root = BoardNode(id=1, fuse_a=63, children=(sub,))

    pass1 = allocate_tree(root, cap(63, 63, 63), DistributionStrategy.EQUAL)
    assert pass1[1] == 14

    pv_station = s(3, mx=32, mn=6)
    pv_root = remaining_capacity_tree(root, pass1, {2: (pv_station,)})
    surplus = 30.0
    pv_capacity = {p: min(surplus, pv_root.fuse_a) for p in PHASES}
    pass2 = allocate_tree(pv_root, pv_capacity, DistributionStrategy.EQUAL)

    assert pass2[3] == 6
    # Kombiniert darf die Unterverteiler-Absicherung nicht überschritten werden
    assert pass1[1] + pass2[3] <= sub.fuse_a + 1e-6


def test_pv_two_pass_zero_surplus_gives_zero():
    grid = s(1, mx=20, mn=6)
    root = BoardNode(id=1, fuse_a=63, stations=(grid,))
    pass1 = allocate_tree(root, cap(63, 63, 63), DistributionStrategy.EQUAL)

    pv_station = s(2, mx=16, mn=6)
    pv_root = remaining_capacity_tree(root, pass1, {1: (pv_station,)})
    pv_capacity = {p: min(0.0, pv_root.fuse_a) for p in PHASES}  # kein Überschuss
    pass2 = allocate_tree(pv_root, pv_capacity, DistributionStrategy.EQUAL)

    assert pass2[2] == 0
