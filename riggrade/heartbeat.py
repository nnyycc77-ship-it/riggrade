"""Subscription heartbeat chain (spec section 7)."""

import hashlib
import secrets


class Heartbeats:
    def __init__(self, store, clock):
        self.store = store
        self.clock = clock

    def _head(self, rig_id):
        return self.store.get("hb:" + rig_id)

    def register(self, rig_id):
        head = {"hash": secrets.token_hex(16), "challenge": secrets.token_hex(8),
                "accepted": (self._head(rig_id) or {}).get("accepted", [])}
        self.store.put("hb:" + rig_id, head)
        return {"next": head["hash"], "challenge": head["challenge"]}

    def beat(self, rig_id, prev, echo):
        """Accept a heartbeat when prev and echo match the head. A wrong prev
        or echo restarts the chain (the gap then counts as down)."""
        head = self._head(rig_id)
        if head is None:
            return {"accepted": False, "error": "unregistered"}
        if prev != head["hash"] or echo != head["challenge"]:
            restarted = self.register(rig_id)
            return {"accepted": False, "error": "chain_broken", **restarted}
        now = self.clock()
        accepted = head["accepted"] + [now]
        cutoff = now - 31 * 86400
        accepted = [t for t in accepted if t >= cutoff]
        new_hash = hashlib.sha256((head["hash"] + "|" + str(now)).encode()).hexdigest()
        head = {"hash": new_hash, "challenge": secrets.token_hex(8),
                "accepted": accepted}
        self.store.put("hb:" + rig_id, head)
        return {"accepted": True, "next": new_hash, "challenge": head["challenge"]}

    def accepted(self, rig_id):
        return list((self._head(rig_id) or {}).get("accepted", []))
