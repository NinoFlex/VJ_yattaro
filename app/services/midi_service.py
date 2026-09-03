import os
os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame
import pygame.midi
from PySide6.QtCore import QObject, Signal, QThread, QTimer

class MidiServiceWorker(QThread):
    notes_received = Signal(int)
    device_connected = Signal()
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
            self.device_connected.emit()
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
            self._running = False
            if self._midi_in:
                try:
                    if pygame.midi.get_init():
                        self._midi_in.close()
                except Exception:
                    pass
            self._midi_in = None
            
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
    status_changed = Signal(str, str)

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
        self._device_name = ""
        self._shutting_down = False
        self._connection_state = "off"
        self._connection_detail = "MIDI: disabled (no input device configured)"
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.setInterval(1000)
        self._reconnect_timer.timeout.connect(self.ensure_connected)
        
    def _set_status(self, state, detail):
        state = str(state or "off").lower()
        detail = str(detail or "")
        if state == self._connection_state and detail == self._connection_detail:
            return
        self._connection_state = state
        self._connection_detail = detail
        self.status_changed.emit(state, detail)

    def connection_status(self):
        return self._connection_state, self._connection_detail

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
        """Apply mappings and keep the configured MIDI input connected."""
        self._mappings = dict(mappings or {})
        self._device_name = str(device_name or "")
        self._shutting_down = False
        print(f"MidiService: Updating config. Device='{self._device_name}', Mappings: {self._mappings}")
        if not self._device_name:
            self.stop(clear_device=True)
            self._set_status("off", "MIDI: disabled (no input device configured)")
            return
        self.ensure_connected(force=True)

    def _find_device_id(self):
        devices = self.get_input_devices()
        if devices:
            print(f"MidiService: Available MIDI inputs: {[name for _, name in devices]}")
        else:
            print("MidiService: No MIDI input devices found")
        for d_id, name in devices:
            if name == self._device_name:
                return d_id
        return -1

    def ensure_connected(self, force=False):
        """Reconnect when the USB MIDI input disappeared or failed during startup."""
        if self._shutting_down or not self._device_name:
            if not self._device_name:
                self._set_status("off", "MIDI: disabled (no input device configured)")
            return False

        worker_alive = bool(self._worker and self._worker.isRunning())
        if worker_alive and not force:
            if self._connection_state != "ok":
                self._set_status("ok", f"MIDI: connected to {self._device_name}")
            return True

        new_id = self._find_device_id()
        if new_id < 0:
            print(f"MidiService: Device '{self._device_name}' not found; retrying in 1s")
            self._current_device_id = -1
            self._set_status(
                "error",
                f"MIDI: configured device not found\nDevice: {self._device_name}\nRetrying automatically",
            )
            if not self._reconnect_timer.isActive():
                self._reconnect_timer.start()
            return False

        if worker_alive and new_id == self._current_device_id:
            return True

        self._connect(new_id)
        return True

    def _connect(self, device_id):
        self.stop(clear_device=False)
        if device_id < 0 or self._shutting_down:
            return

        print(f"MidiService: Connecting to device ID {device_id} ({self._device_name})")
        self._current_device_id = device_id
        self._set_status(
            "warn",
            f"MIDI: connecting\nDevice: {self._device_name}\nPort ID: {device_id}",
        )
        worker = MidiServiceWorker(device_id)
        self._worker = worker
        worker.notes_received.connect(self._on_note_received)
        worker.device_connected.connect(self._on_device_connected)
        worker.device_disconnected.connect(self._on_device_disconnected)
        worker.finished.connect(lambda: self._on_worker_finished(worker))
        worker.start()

    def _on_device_connected(self):
        if self._worker is None:
            return
        self._set_status(
            "ok",
            f"MIDI: connected\nDevice: {self._device_name}\nPort ID: {self._current_device_id}",
        )

    def _on_device_disconnected(self):
        print("MidiService: MIDI worker reported a disconnect/error")
        self._current_device_id = -1
        self._set_status(
            "error",
            f"MIDI: disconnected/error\nDevice: {self._device_name}\nRetrying automatically",
        )
        if not self._shutting_down and self._device_name and not self._reconnect_timer.isActive():
            self._reconnect_timer.start()

    def _on_worker_finished(self, worker):
        if self._worker is worker:
            self._worker = None
        if not self._shutting_down and self._device_name and not self._reconnect_timer.isActive():
            self._reconnect_timer.start()

    def stop(self, clear_device=False):
        if self._reconnect_timer.isActive():
            self._reconnect_timer.stop()
        worker = self._worker
        self._worker = None
        if worker:
            print("MidiService: Stopping worker...")
            worker.stop()
        self._current_device_id = -1
        if clear_device:
            self._device_name = ""
            self._set_status("off", "MIDI: disabled (no input device configured)")

    def shutdown(self):
        self._shutting_down = True
        self.stop(clear_device=True)

    def _on_note_received(self, note):
        print(f"MidiService: Raw Note received: {note}")
        self.raw_note_received.emit(note)
        action = self._mappings.get(note)
        if action:
            print(f"MidiService: Note {note} mapped to {action}")
            sig = getattr(self, action + "_triggered", None)
            if sig:
                sig.emit()
