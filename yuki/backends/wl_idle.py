"""Inactivité clavier + souris sous Wayland, via le protocole ext-idle-notify-v1 (ctypes).

KWin ne donne pas GetSessionIdleTime sous Wayland. On ouvre donc une connexion Wayland à nous,
séparée de celle de Qt, et on demande à être prévenu après IDLE_MS sans saisie. La requête v1
respecte les inhibitions : pas de givre pendant une vidéo.
"""

import ctypes as C
import time

from PyQt6.QtCore import QSocketNotifier

IDLE_MS = 5000


class _Message(C.Structure):
    _fields_ = [("name", C.c_char_p), ("signature", C.c_char_p), ("types", C.POINTER(C.c_void_p))]


class _Interface(C.Structure):
    _fields_ = [("name", C.c_char_p), ("version", C.c_int), ("method_count", C.c_int),
                ("methods", C.POINTER(_Message)), ("event_count", C.c_int),
                ("events", C.POINTER(_Message))]


GLOBAL = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_uint32, C.c_char_p, C.c_uint32)
REMOVE = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p, C.c_uint32)
EVENT = C.CFUNCTYPE(None, C.c_void_p, C.c_void_p)


class WaylandIdle:
    def __init__(self):
        lib = self.lib = C.CDLL("libwayland-client.so.0")
        lib.wl_display_connect.restype = C.c_void_p
        lib.wl_display_connect.argtypes = [C.c_char_p]
        lib.wl_proxy_marshal_flags.restype = C.c_void_p
        lib.wl_proxy_add_listener.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p]
        for name in ("wl_display_roundtrip", "wl_display_get_fd", "wl_display_prepare_read",
                     "wl_display_read_events", "wl_display_dispatch_pending", "wl_display_flush",
                     "wl_display_disconnect"):
            getattr(lib, name).argtypes = [C.c_void_p]
        self.idle_since = None
        self._keep = []           # ctypes : les structures et rappels doivent rester en vie
        self.display = lib.wl_display_connect(None)
        if not self.display:
            raise OSError("pas de connexion Wayland")
        try:
            self._setup()
        except Exception:
            lib.wl_display_disconnect(self.display)
            raise
        self.notifier = QSocketNotifier(lib.wl_display_get_fd(self.display), QSocketNotifier.Type.Read)
        self.notifier.activated.connect(self._dispatch)

    def _setup(self):
        lib = self.lib
        seat_if = C.addressof(C.c_void_p.in_dll(lib, "wl_seat_interface"))
        registry_if = C.addressof(C.c_void_p.in_dll(lib, "wl_registry_interface"))
        none = (C.c_void_p * 1)(None)
        notif_if = _Interface(b"ext_idle_notification_v1", 1, 1, (_Message * 1)(_Message(b"destroy", b"", none)),
                              2, (_Message * 2)(_Message(b"idled", b"", none), _Message(b"resumed", b"", none)))
        get_types = (C.c_void_p * 3)(C.addressof(notif_if), None, seat_if)
        notifier_if = _Interface(b"ext_idle_notifier_v1", 1, 2,
                                 (_Message * 2)(_Message(b"destroy", b"", none),
                                                _Message(b"get_idle_notification", b"nuo", get_types)),
                                 0, None)
        self._keep += [none, notif_if, get_types, notifier_if]

        found = {}

        def on_global(_data, _registry, name, interface, _version):
            if interface in (b"wl_seat", b"ext_idle_notifier_v1") and interface not in found:
                found[interface] = name
        callbacks = [GLOBAL(on_global), REMOVE(lambda *_: None)]
        listener = (C.c_void_p * 2)(*[C.cast(cb, C.c_void_p) for cb in callbacks])
        self._keep += [callbacks, listener]
        registry = lib.wl_proxy_marshal_flags(C.c_void_p(self.display), C.c_uint32(1), C.c_void_p(registry_if),
                                              C.c_uint32(1), C.c_uint32(0), C.c_void_p(None))
        lib.wl_proxy_add_listener(registry, listener, None)
        if lib.wl_display_roundtrip(self.display) < 0 or len(found) < 2:
            raise OSError("ext-idle-notify-v1 absent")

        def bind(interface, name, iface_name):
            return lib.wl_proxy_marshal_flags(C.c_void_p(registry), C.c_uint32(0), C.c_void_p(interface),
                                              C.c_uint32(1), C.c_uint32(0), C.c_uint32(name),
                                              C.c_char_p(iface_name), C.c_uint32(1), C.c_void_p(None))
        seat = bind(seat_if, found[b"wl_seat"], b"wl_seat")
        notifier = bind(C.addressof(notifier_if), found[b"ext_idle_notifier_v1"], b"ext_idle_notifier_v1")
        notification = lib.wl_proxy_marshal_flags(
            C.c_void_p(notifier), C.c_uint32(1), C.c_void_p(C.addressof(notif_if)), C.c_uint32(1),
            C.c_uint32(0), C.c_void_p(None), C.c_uint32(IDLE_MS), C.c_void_p(seat))

        def idled(_data, _obj):
            self.idle_since = time.monotonic() - IDLE_MS / 1000

        def resumed(_data, _obj):
            self.idle_since = None
        events = [EVENT(idled), EVENT(resumed)]
        listener = (C.c_void_p * 2)(*[C.cast(cb, C.c_void_p) for cb in events])
        self._keep += [events, listener]
        lib.wl_proxy_add_listener(notification, listener, None)
        if lib.wl_display_roundtrip(self.display) < 0:
            raise OSError("ext-idle-notify-v1 refusé")

    def _dispatch(self):
        lib, d = self.lib, self.display
        if lib.wl_display_prepare_read(d) == 0 and lib.wl_display_read_events(d) < 0:
            self.notifier.setEnabled(False)     # connexion perdue : on garde la dernière valeur
            return
        lib.wl_display_dispatch_pending(d)
        lib.wl_display_flush(d)

    def seconds(self):
        return 0.0 if self.idle_since is None else time.monotonic() - self.idle_since

    def close(self):
        if self.display:
            self.notifier.setEnabled(False)
            self.lib.wl_display_disconnect(self.display)
            self.display = None
