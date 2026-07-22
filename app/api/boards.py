"""REST-Endpunkte für die Verteilungshierarchie (Hauptverteilung/Unterverteilung).

Bildet die reale Elektroinstallation ab: Hauptverteilung → Unterverteilungen
→ Abgänge zu den Ladestationen, jeweils mit eigener Absicherung. Siehe
app.models.distribution_board und app.loadmanager.engine.allocate_tree.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.loadmanager.engine import PHASES
from app.loadmanager.loop import service
from app.models import ChargePoint, DistributionBoard
from app.schemas import (
    BoardTreeNode,
    BoardTreeStation,
    DistributionBoardCreate,
    DistributionBoardRead,
    DistributionBoardUpdate,
)

router = APIRouter(prefix="/api/boards", tags=["Verteilungshierarchie"])


def _ancestor_ids(session: Session, board_id: int) -> set[int]:
    """IDs von ``board_id`` und all seinen Vorfahren bis zur Wurzel (Zyklenprüfung)."""
    ids: set[int] = set()
    current: DistributionBoard | None = session.get(DistributionBoard, board_id)
    while current is not None:
        ids.add(current.id)
        current = (
            session.get(DistributionBoard, current.parent_board_id)
            if current.parent_board_id is not None
            else None
        )
    return ids


def _validate_parent(session: Session, parent_board_id: int, self_id: int | None) -> None:
    parent = session.get(DistributionBoard, parent_board_id)
    if parent is None:
        raise HTTPException(400, f"Übergeordneter Verteiler {parent_board_id} existiert nicht")
    if self_id is not None and (
        parent_board_id == self_id or self_id in _ancestor_ids(session, parent_board_id)
    ):
        raise HTTPException(400, "Das würde einen Kreis im Verteilungsbaum erzeugen")


@router.get("", response_model=list[DistributionBoardRead])
def list_boards(session: Session = Depends(get_session)):
    DistributionBoard.get_or_create_root(session)
    return session.query(DistributionBoard).order_by(DistributionBoard.name).all()


@router.post("", response_model=DistributionBoardRead, status_code=201)
def create_board(data: DistributionBoardCreate, session: Session = Depends(get_session)):
    if data.parent_board_id is None:
        raise HTTPException(
            400,
            "Neue Verteiler benötigen einen übergeordneten Verteiler "
            "(die Hauptverteilung wird automatisch verwaltet)",
        )
    _validate_parent(session, data.parent_board_id, self_id=None)
    board = DistributionBoard(**data.model_dump())
    session.add(board)
    session.commit()
    session.refresh(board)
    return board


@router.get("/tree", response_model=BoardTreeNode)
def get_tree(session: Session = Depends(get_session)):
    """Kompletter Verteilungsbaum inkl. aktueller Live-Auslastung je Phase."""
    root = DistributionBoard.get_or_create_root(session)
    children_of: dict[int, list[DistributionBoard]] = {}
    for b in session.query(DistributionBoard).all():
        if b.parent_board_id is not None:
            children_of.setdefault(b.parent_board_id, []).append(b)
    cps_of: dict[int | None, list[ChargePoint]] = {}
    for cp in session.query(ChargePoint).all():
        cps_of.setdefault(cp.distribution_board_id, []).append(cp)

    snap_cps = service.snapshot.get("charge_points", {})

    def build(board: DistributionBoard) -> BoardTreeNode:
        # Ladepunkte ohne Zuweisung (distribution_board_id IS NULL) hängen
        # implizit an der Wurzel – siehe app.loadmanager.loop._cycle.
        my_cps = list(cps_of.get(board.id, []))
        if board.parent_board_id is None:
            my_cps += cps_of.get(None, [])

        load = {p: 0.0 for p in PHASES}
        station_nodes = []
        for cp in my_cps:
            live = snap_cps.get(cp.id, {})
            online = bool(live.get("online"))
            setpoint = live.get("setpoint_a") or 0.0
            if online:
                for p in cp.phases:
                    load[p] += setpoint
            station_nodes.append(
                BoardTreeStation(
                    id=cp.id,
                    name=cp.name,
                    circuit_breaker_a=cp.circuit_breaker_a,
                    max_current_a=cp.max_current_a,
                    online=online,
                    setpoint_a=live.get("setpoint_a"),
                    pv_surplus_only=cp.pv_surplus_only,
                )
            )

        child_nodes = []
        for child in sorted(children_of.get(board.id, []), key=lambda b: b.name):
            child_node = build(child)
            for p in PHASES:
                load[p] += child_node.load_a[p]
            child_nodes.append(child_node)

        return BoardTreeNode(
            id=board.id,
            name=board.name,
            incoming_fuse_a=board.incoming_fuse_a,
            priority=board.priority,
            strategy=board.strategy,
            location=board.location,
            load_a=load,
            stations=station_nodes,
            children=child_nodes,
        )

    return build(root)


@router.get("/{board_id}", response_model=DistributionBoardRead)
def get_board(board_id: int, session: Session = Depends(get_session)):
    board = session.get(DistributionBoard, board_id)
    if board is None:
        raise HTTPException(404, "Verteiler nicht gefunden")
    return board


@router.put("/{board_id}", response_model=DistributionBoardRead)
def update_board(board_id: int, data: DistributionBoardUpdate, session: Session = Depends(get_session)):
    board = session.get(DistributionBoard, board_id)
    if board is None:
        raise HTTPException(404, "Verteiler nicht gefunden")
    if (board.parent_board_id is None) != (data.parent_board_id is None):
        raise HTTPException(
            400, "Der Wurzel-Status eines Verteilers (Hauptverteilung) kann nicht geändert werden"
        )
    if data.parent_board_id is not None:
        _validate_parent(session, data.parent_board_id, self_id=board_id)
    for key, value in data.model_dump().items():
        setattr(board, key, value)
    session.commit()
    session.refresh(board)
    return board


@router.delete("/{board_id}", status_code=204)
def delete_board(board_id: int, session: Session = Depends(get_session)):
    board = session.get(DistributionBoard, board_id)
    if board is None:
        raise HTTPException(404, "Verteiler nicht gefunden")
    if board.parent_board_id is None:
        raise HTTPException(409, "Die Hauptverteilung kann nicht gelöscht werden")
    if session.query(DistributionBoard).filter_by(parent_board_id=board_id).count():
        raise HTTPException(409, "Verteiler hat noch Unterverteilungen")
    if session.query(ChargePoint).filter_by(distribution_board_id=board_id).count():
        raise HTTPException(409, "Verteiler hat noch zugewiesene Ladepunkte")
    session.delete(board)
    session.commit()
    return None
