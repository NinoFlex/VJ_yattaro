from pyrekordbox.db6 import Rekordbox6Database, DjmdContent, DjmdSongHistory, DjmdArtist
import os
import shutil
import tempfile

def fetch_latest_10():
    db_path = "/mnt/j/PIONEER/Master/master.db"
    temp_dir = tempfile.mkdtemp()
    local_db = os.path.join(temp_dir, "master.db")
    shutil.copy2(db_path, local_db)
    
    try:
        db = Rekordbox6Database(local_db)
        session = db.session
        
        # DjmdSongHistory.ID 順と created_at 順の両方で確認してみる
        print("--- Fetching by created_at DESC ---")
        query = (
            session.query(DjmdSongHistory.ID, DjmdSongHistory.created_at, DjmdArtist.Name, DjmdContent.Title)
            .join(DjmdContent, DjmdSongHistory.ContentID == DjmdContent.ID)
            .join(DjmdArtist, DjmdContent.ArtistID == DjmdArtist.ID)
            .order_by(DjmdSongHistory.created_at.desc())
            .limit(10)
        )
        
        for hid, c_at, artist, title in query.all():
            print(f"ID:{hid} [{c_at}] {artist} - {title}")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        shutil.rmtree(temp_dir)

if __name__ == "__main__":
    fetch_latest_10()
