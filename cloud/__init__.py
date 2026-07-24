"""Voltibus Cloud: separater, mandantenfähiger Dienst für die Fernansicht
eigener Lastmanagement-Installationen ("Phone-Home").

Bewusst getrennt von ``app/`` (dem eigentlichen, single-tenant Lastmanagement
auf dem Raspberry Pi): dieser Dienst läuft auf einem anderen Host, den der
Betreiber selbst bereitstellt (siehe README-cloud.md). Er speichert nur
Kontodaten und den jeweils letzten Status je Installation – keine
Modbus-Kommunikation, kein Regelzyklus.
"""

__version__ = "0.1.0"
