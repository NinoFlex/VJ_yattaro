import sqlite3
import os

# Rekordbox 6/7 common encryption key
key = "402fd482c388465cae5c3dae4e402350"
db_path = "/mnt/j/PIONEER/Master/master.db"

def test_nolock_direct():
    try:
        # uri=True を使い、nolock=1 を指定して直接読み取りを試行
        # これにより WSL/Windows のロック競合を回避できる可能性がある
        conn = sqlite3.connect(f"file:{db_path}?mode=ro&nolock=1", uri=True)
        cursor = conn.cursor()
        
        # SQLCipher のキー設定
        cursor.execute(f"PRAGMA key = \"x'{key}'\";")
        
        # テーブル一覧取得または履歴取得を試行
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        
        if tables:
            print("Direct read success with nolock=1!")
            # 最新の1件だけ確認
            cursor.execute("""
                SELECT c.Title, a.Name, h.created_at 
                FROM djmdSongHistory AS h
                JOIN djmdContent AS c ON h.ContentID = c.ID
                JOIN djmdArtist AS a ON c.ArtistID = a.ID
                ORDER BY h.created_at DESC LIMIT 1;
            """)
            res = cursor.fetchone()
            print(f"Latest: [{res[2]}] {res[1]} - {res[0]}")
        else:
            print("No tables found (Decryption failed or DB empty)")
            
        conn.close()
    except Exception as e:
        print(f"Direct read failed with nolock=1: {e}")

if __name__ == "__main__":
    test_nolock_direct()
