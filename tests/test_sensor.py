"""Guards the sensor's device/state class pairing, which HA validates at runtime."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.pricewatcha.sensor import PricewatchaPriceSensor


def _declared(name: str):
    """Read a declared _attr_* value on both HA layouts.

    Newer Home Assistant turns `_attr_x` into a property and keeps the value in
    `__attr_x`; older versions keep `_attr_x` as a plain class attribute.
    """
    own = vars(PricewatchaPriceSensor)
    for key in (f"__attr_{name}", f"_attr_{name}"):
        value = own.get(key)
        if value is not None and not isinstance(value, property):
            return value
    return None


def test_state_class_is_allowed_for_monetary():
    assert _declared("device_class") is SensorDeviceClass.MONETARY
    # HA rejects 'measurement' for monetary sensors; only None or TOTAL are legal.
    assert _declared("state_class") in (None, SensorStateClass.TOTAL)
