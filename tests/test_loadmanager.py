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

def subtree_phase_totals(node: BoardNode, assignment: dict) -> dict:
    """Summiert die Ströme aller Stationen im Teilbaum eines Knotens je Phase."""
    totals = {p: 0.0 for p in PHASES}
    for st in node.stations:
        v = assignment.get(st.id, 0.0)
        for p in st.phases:
            totals[p] += v
    for child in node.children:
        child_totals = subtree_phase_totals(child, assignment)
        for p in PHASES:
            totals[p] += child_totals[p]
    return totals


def assert_tree_within_limits(node: BoardNode, assignment: dict) -> None:
    """Prüft rekursiv, dass an JEDEM Knoten des Baums dessen Absicherung
    (fuse_a) je Phase eingehalten wird – die zentrale Garantie von
    allocate_tree()."""
    totals = subtree_phase_totals(node, assignment)
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
