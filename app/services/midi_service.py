import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame
import pygame.midi
from PySide6.QtCore import QObject, Signal, QThread

class MidiServiceWorker(QThread):
    notes_received = Signal(int)
    device_disconnected = Signal()
    
    def __init__(self, device_id):
        super().__init__()
        self.device_id = device_id
        self._running = False
        self._midi_in = None
        
    def run(self):
        try:
            if not pygame.midi.get_init():
                pygame.midi.init()
            self._midi_in = pygame.midi.Input(self.device_id)
            self._running = True
            print(f"MidiServiceWorker: Started listening to device ID {self.device_id}")
            
            while self._running:
                if self._midi_in.poll():
                    events = self._midi_in.read(10)
                    for event in events:
                        # event structure: [[status, data1, data2, data3], timestamp]
                        data = event[0]
                        status = data[0]
                        note = data[1]
                        velocity = data[2]
                        
                        # Note On check: status is 144 (0x90, Ch1) to 159 (0x9F, Ch16)
                        # And velocity > 0
                        if 144 <= status <= 159 and velocity > 0:
                            self.notes_received.emit(note)
                self.msleep(10) # 10ms poll
                
        except Exception as e:
            print(f"MidiServiceWorker Error: {e}")
            self.device_disconnected.emit()
        finally:
            if self._midi_in:
                try:
                    if pygame.midi.get_init():
                        self._midi_in.close()
                except Exception:
                    pass
            
    def stop(self):
        self._running = False
        self.wait()

class MidiService(QObject):
    _instance = None
    
    # Signals
    raw_note_received = Signal(int)
    move_up_triggered = Signal()
    move_down_triggered = Signal()
    move_left_triggered = Signal()
    move_right_triggered = Signal()
    preload_triggered = Signal()
    play_triggered = Signal()
    search_triggered = Signal()
    rewind_triggered = Signal()
    forward_triggered = Signal()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MidiService, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if getattr(self, '_initialized', False):
            return
        super().__init__()
        self._initialized = True
        self._current_device_id = -1
        self._worker = None
        self._mappings = {} # {note: action_name}
        
    def get_input_devices(self):
        """Returns list of tuples: [(id, name), ...]"""
        devices = []
        try:
            if not pygame.midi.get_init():
                pygame.midi.init()
            count = pygame.midi.get_count()
            for i in range(count):
                info = pygame.midi.get_device_info(i)
                if info and info[2] == 1: # Is Input
                    devices.append((i, info[1].decode('utf-8')))
        except Exception as e:
            print(f"MidiService: Failed to get devices: {e}")
        return devices

    def set_config(self, device_name, mappings):
        """mappings = dict of {note: action_name}"""
        self._mappings = mappings
        print(f"MidiService: Updating config. Mappings: {mappings}")
        
        # find device id by name
        devices = self.get_input_devices()
        new_id = -1
        for d_id, name in devices:
            if name == device_name:
                new_id = d_id
                break
                
        if new_id != self._current_device_id and new_id != -1:
            self._connect(new_id)
        elif new_id == -1 and device_name:
            print(f"MidiService: Device '{device_name}' not found.")
            self.stop()
        elif not device_name:
            self.stop()
            
    def _connect(self, device_id):
        self.stop()
        if device_id < 0:
            return
            
        print(f"MidiService: Connecting to device ID {device_id}")
        self._current_device_id = device_id
        self._worker = MidiServiceWorker(device_id)
        self._worker.notes_received.connect(self._on_note_received)
        self._worker.start()
        
    def stop(self):
        if self._worker:
            print("MidiService: Stopping worker...")
            self._worker.stop()
            self._worker = None
        self._current_device_id = -1

    def _on_note_received(self, note):
        print(f"MidiService: Raw Note received: {note}")
        self.raw_note_received.emit(note)
        action = self._mappings.get(note)
        if action:
            print(f"MidiService: Note {note} mapped to {action}")
            sig = getattr(self, action + "_triggered", None)
            if sig:
                sig.emit()
