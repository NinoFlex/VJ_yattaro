from pyrekordbox.db6 import Rekordbox6Database, DjmdContent, DjmdSongHistory, DjmdArtist

def test_local_db():
    db_path = "/tmp/master.db"
    try:
        db = Rekordbox6Database(db_path)
        session = db.session
        query = (
            session.query(DjmdContent.Title, DjmdArtist.Name, DjmdSongHistory.created_at)
            .join(DjmdSongHistory, DjmdSongHistory.ContentID == DjmdContent.ID)
            .join(DjmdArtist, DjmdContent.ArtistID == DjmdArtist.ID)
            .order_by(DjmdSongHistory.created_at.desc())
            .limit(5)
        )
        results = query.all()
        print("--- Local DB Results ---")
        for r in results:
            print(f"[{r[2]}] {r[1]} - {r[0]}")
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_local_db()
