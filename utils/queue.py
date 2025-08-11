import wavelink
from typing import List, Optional
from collections import deque

class MusicQueue:
    """Music queue management system"""
    
    def __init__(self):
        self.queue = deque()
        self.current_track = None
        self.history = deque(maxlen=50)  # Keep last 50 tracks
        
    def add_track(self, track: wavelink.Playable):
        """Add track to queue"""
        self.queue.append(track)
        
    def get_next(self) -> Optional[wavelink.Playable]:
        """Get next track from queue"""
        if self.current_track:
            self.history.append(self.current_track)
            
        if self.queue:
            self.current_track = self.queue.popleft()
            return self.current_track
        else:
            self.current_track = None
            return None
            
    def get_current(self) -> Optional[wavelink.Playable]:
        """Get current playing track"""
        return self.current_track
        
    def set_current(self, track: wavelink.Playable):
        """Set current track (for immediate play)"""
        if self.current_track:
            self.history.append(self.current_track)
        self.current_track = track
        
    def get_upcoming(self, limit: int = 10) -> List[wavelink.Playable]:
        """Get upcoming tracks in queue"""
        return list(self.queue)[:limit]
        
    def get_history(self, limit: int = 10) -> List[wavelink.Playable]:
        """Get recently played tracks"""
        return list(self.history)[-limit:]
        
    def clear(self):
        """Clear queue and current track"""
        self.queue.clear()
        self.current_track = None
        
    def remove(self, index: int) -> Optional[wavelink.Playable]:
        """Remove track at index from queue"""
        try:
            if 0 <= index < len(self.queue):
                track = self.queue[index]
                del self.queue[index]
                return track
            return None
        except IndexError:
            return None
            
    def shuffle(self):
        """Shuffle the queue"""
        import random
        queue_list = list(self.queue)
        random.shuffle(queue_list)
        self.queue = deque(queue_list)
        
    def size(self) -> int:
        """Get queue size"""
        return len(self.queue)
        
    def is_empty(self) -> bool:
        """Check if queue is empty"""
        return len(self.queue) == 0 and self.current_track is None
        
    def peek(self, index: int = 0) -> Optional[wavelink.Playable]:
        """Peek at track in queue without removing it"""
        try:
            return self.queue[index]
        except IndexError:
            return None
