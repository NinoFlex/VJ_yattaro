"""
ダミーのYouTube検索結果を表示（テスト用）
"""

def show_dummy_youtube_results(self):
    """ダミーのYouTube検索結果を表示（テスト用）"""
    import random
    
    dummy_videos = []
    for i in range(5):
        dummy_videos.append({
            'video_id': f'dummy_{i}',
            'title': f'Test Video {i+1}',
            'thumbnail': None,  # 後でサムネイルを設定
            'duration': f'{random.randint(2,10)}:{random.randint(10,59):02d}',
            'url': f'https://youtube.com/watch?v=dummy_{i}'
        })
    
    self.left_pane.set_search_results(dummy_videos)
    print("UI: Displaying dummy YouTube results")
