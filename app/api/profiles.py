"""REST-Endpunkte für Geräteprofile inkl. Import/Export."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import DeviceProfile, RegisterMapping
from app.schemas import (
    DeviceProfileCreate,
    DeviceProfileRead,
    DeviceProfileUpdate,
    ProfileImport,
    RegisterMappingCreate,
)

router = APIRouter(prefix="/api/profiles", tags=["Geräteprofile"])


def _apply_registers(profile: DeviceProfile, registers: list[RegisterMappingCreate]) -> None:
    """Ersetzt die Register eines Profils vollständig."""
    profile.registers.clear()
    for r in registers:
        profile.registers.append(
            RegisterMapping(
                key=r.key,
                role=r.role,
                register_address=r.register_address,
                function_code=r.function_code,
                data_type=r.data_type,
                byte_order=r.byte_order,
                word_order=r.word_order,
                scale=r.scale,
                offset=r.offset,
                unit=r.unit,
                enum_map=r.enum_map,
                writable_min=r.writable_min,
                writable_max=r.writable_max,
            )
        )


@router.get("", response_model=list[DeviceProfileRead])
def list_profiles(session: Session = Depends(get_session)):
    return session.query(DeviceProfile).order_by(DeviceProfile.name).all()


@router.get("/{profile_id}", response_model=DeviceProfileRead)
def get_profile(profile_id: int, session: Session = Depends(get_session)):
    profile = session.get(DeviceProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Geräteprofil nicht gefunden")
    return profile


@router.post("", response_model=DeviceProfileRead, status_code=201)
def create_profile(data: DeviceProfileCreate, session: Session = Depends(get_session)):
    profile = DeviceProfile(
        name=data.name,
        manufacturer=data.manufacturer,
        notes=data.notes,
        default_unit_id=data.default_unit_id,
        byte_order=data.byte_order,
        word_order=data.word_order,
    )
    _apply_registers(profile, data.registers)
    session.add(profile)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, f"Profilname '{data.name}' existiert bereits")
    session.refresh(profile)
    return profile


@router.put("/{profile_id}", response_model=DeviceProfileRead)
def update_profile(profile_id: int, data: DeviceProfileUpdate, session: Session = Depends(get_session)):
    profile = session.get(DeviceProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Geräteprofil nicht gefunden")
    profile.name = data.name
    profile.manufacturer = data.manufacturer
    profile.notes = data.notes
    profile.default_unit_id = data.default_unit_id
    profile.byte_order = data.byte_order
    profile.word_order = data.word_order
    if data.registers is not None:
        _apply_registers(profile, data.registers)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, f"Profilname '{data.name}' existiert bereits")
    session.refresh(profile)
    return profile


@router.delete("/{profile_id}", status_code=204)
def delete_profile(profile_id: int, session: Session = Depends(get_session)):
    profile = session.get(DeviceProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Geräteprofil nicht gefunden")
    # Verhindern, dass ein von Stationen genutztes Profil gelöscht wird
    from app.models import ChargingStation

    in_use = session.query(ChargingStation).filter_by(profile_id=profile_id).count()
    if in_use:
        raise HTTPException(409, f"Profil wird von {in_use} Station(en) verwendet")
    session.delete(profile)
    session.commit()
    return None


# --- Import / Export -------------------------------------------------------

@router.post("/import", response_model=DeviceProfileRead, status_code=201)
def import_profile(data: ProfileImport, session: Session = Depends(get_session)):
    """Importiert ein Profil aus dem kompakten JSON-Format."""
    profile = DeviceProfile(
        name=data.name,
        manufacturer=data.manufacturer,
        notes=data.notes,
        default_unit_id=data.default_unit_id,
        byte_order=data.byte_order,
        word_order=data.word_order,
    )
    _apply_registers(profile, data.registers)
    session.add(profile)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, f"Profilname '{data.name}' existiert bereits")
    session.refresh(profile)
    return profile


@router.get("/{profile_id}/export")
def export_profile(profile_id: int, session: Session = Depends(get_session)):
    """Exportiert ein Profil als kompaktes JSON (import-kompatibel)."""
    profile = session.get(DeviceProfile, profile_id)
    if profile is None:
        raise HTTPException(404, "Geräteprofil nicht gefunden")
    payload = {
        "name": profile.name,
        "manufacturer": profile.manufacturer,
        "notes": profile.notes,
        "default_unit_id": profile.default_unit_id,
        "byte_order": profile.byte_order.value,
        "word_order": profile.word_order.value,
        "registers": [
            {
                "key": r.key,
                "role": r.role.value,
                "function_code": r.function_code,
                "register_address": r.register_address,
                "data_type": r.data_type.value,
                **({"byte_order": r.byte_order.value} if r.byte_order else {}),
                **({"word_order": r.word_order.value} if r.word_order else {}),
                "scale": r.scale,
                "offset": r.offset,
                **({"unit": r.unit} if r.unit else {}),
                **({"enum_map": r.enum_map} if r.enum_map else {}),
                **({"writable_min": r.writable_min} if r.writable_min is not None else {}),
                **({"writable_max": r.writable_max} if r.writable_max is not None else {}),
            }
            for r in profile.registers
        ],
    }
    headers = {"Content-Disposition": f'attachment; filename="profile_{profile_id}.json"'}
    return JSONResponse(payload, headers=headers)
