"""Peer classes, v0 (spec section 2)."""

DATA_CENTRE = "data_centre"
CONSUMER = "consumer"

# class id: (chip, form, nominal memory GB, price tier)
PEER_CLASSES = {
    "h200-sxm-141": ("H200", "SXM", 141, DATA_CENTRE),
    "h100-sxm-80": ("H100", "SXM", 80, DATA_CENTRE),
    "h100-pcie-80": ("H100", "PCIe", 80, DATA_CENTRE),
    "a100-sxm-80": ("A100", "SXM", 80, DATA_CENTRE),
    "a100-pcie-80": ("A100", "PCIe", 80, DATA_CENTRE),
    "a100-pcie-40": ("A100", "PCIe", 40, DATA_CENTRE),
    "l40s-48": ("L40S", "PCIe", 48, DATA_CENTRE),
    "rtx5090-32": ("RTX 5090", "PCIe", 32, CONSUMER),
    "rtx4090-24": ("RTX 4090", "PCIe", 24, CONSUMER),
    "rtx3090-24": ("RTX 3090", "PCIe", 24, CONSUMER),
}

WORKLOAD_CLASSES = ("inference", "training")


def price_tier(class_id):
    return PEER_CLASSES[class_id][3]


def nominal_memory_gb(class_id):
    return PEER_CLASSES[class_id][2]


def label_name(class_id):
    chip, _form, _mem, _tier = PEER_CLASSES[class_id]
    return chip


def identify(device_name, memory_gb):
    """Return the class id for a reported device, or None (unknown_class).

    Matching is on the chip name, the form and the reported memory, which must
    be within 10% of the class nominal so 40 GB and 80 GB parts never collide.
    Driver names mark the PCIe form ("NVIDIA H100 PCIe", "NVIDIA A100-PCIE-40GB")
    but not always the SXM form ("NVIDIA H100 80GB HBM3"), so for chips sold in
    both forms the PCIe class needs "PCIE" in the name and the SXM class needs
    it absent.
    """
    name = (device_name or "").upper()
    if "NVL" in name:  # NVL parts (for example H200 NVL) are not a v0 class
        return None
    pcie = "PCIE" in name
    for class_id, (chip, form, mem, _tier) in PEER_CLASSES.items():
        if chip.upper() not in name:
            continue
        if chip in DUAL_FORM_CHIPS and (form == "PCIe") != pcie:
            continue
        if memory_gb and abs(memory_gb - mem) <= 0.10 * mem:
            return class_id
    return None


DUAL_FORM_CHIPS = {chip for chip, form, _m, _t in PEER_CLASSES.values()
                   if form == "SXM"} & {chip for chip, form, _m, _t in
                                        PEER_CLASSES.values() if form == "PCIe"}
