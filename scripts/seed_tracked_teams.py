from sqlalchemy import select

from app.db.session import SessionLocal
from app.db.models.tracked_team import TrackedTeam

'''
script to seed top 30 teams into db until automatic scraping can be done - maybe need to update every once in a while in case of big vrs shift
'''

TOP_TEAMS = [
    "Furia",
    "Vitality",
    "Parivision",
    "Falcons",
    "Navi",
    "Spirit",
    "Mouz",
    "FaZe",
    "Aurora",
    "Mongolz",
    "B8",
    "Liquid",
    "G2",
    "3DMax",
    "Astralis",
    "Legacy",
    "NRG",
    "GamerLegion",
    "FUT",
    "Pain",
    "BC.Game",
    "NIP",
    "Heroic",
    "M80",
    "BetBoom",
    "Gentle Mates",
    "HOTU",
    "Passion UA",
    "MIBR",
    "FlyQuest"
]

def main() -> None:
    db = SessionLocal()
    try:
        existing = {t.name for t in db.scalars(select(TrackedTeam)).all()}
        to_add = [TrackedTeam(name=name) for name in TOP_TEAMS if name not in existing]

        db.add_all(to_add)
        db.commit()
        print(f"Seeded {len(to_add)} tracked teams")
    finally:
        db.close()

if __name__ == "__main__":
    main()
