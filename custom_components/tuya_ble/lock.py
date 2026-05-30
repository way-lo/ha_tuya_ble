"""Tuya BLE lock platform."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import (
    LockEntity,
    LockEntityDescription,
    LockEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, DPCode
from .devices import (
    TuyaBLECoordinator,
    TuyaBLEData,
    TuyaBLEEntity,
    TuyaBLEProductInfo,
    TuyaBLESmartLockInfo,
    get_device_product_info,
)
from .tuya_ble import TuyaBLEDataPointType, TuyaBLEDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE lock entities."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    product = get_device_product_info(data.device)

    if product is None:
        return

    entities: list[LockEntity] = []

    if product.smartlock is not None:
        entities.append(
            TuyaBLESmartLock(hass, data.coordinator, data.device, product)
        )
    elif product.lock:
        entities.append(
            TuyaBLELock(hass, data.coordinator, data.device, product)
        )

    if entities:
        async_add_entities(entities)


class TuyaBLELock(TuyaBLEEntity, LockEntity):
    """Lock entity using the original fixed LOCK_MOTOR_STATE / MANUAL_LOCK DPs."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
    ) -> None:
        super().__init__(
            hass,
            coordinator,
            device,
            product,
            LockEntityDescription(key="lock", name=product.name),
        )
        self._attr_supported_features = LockEntityFeature.OPEN

    @property
    def is_locked(self) -> bool | None:
        """Return true if lock is locked."""
        if motor_state := self._device.datapoints.get_or_create(
            DPCode.LOCK_MOTOR_STATE, TuyaBLEDataPointType.DT_BOOL, False
        ):
            return not motor_state.value
        return None

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the lock."""
        if manual_lock := self._device.datapoints.get_or_create(
            DPCode.MANUAL_LOCK, TuyaBLEDataPointType.DT_BOOL, True
        ):
            await manual_lock.set_value(True)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the lock."""
        if manual_lock := self._device.datapoints.get_or_create(
            DPCode.MANUAL_LOCK, TuyaBLEDataPointType.DT_BOOL, False
        ):
            await manual_lock.set_value(False)

    async def async_open(self, **kwargs: Any) -> None:
        """Open (momentary unlock)."""
        if manual_lock := self._device.datapoints.get_or_create(
            DPCode.MANUAL_LOCK, TuyaBLEDataPointType.DT_BOOL, False
        ):
            await manual_lock.set_value(False)


class TuyaBLESmartLock(TuyaBLEEntity, LockEntity):
    """Lock entity for smart locks described by TuyaBLESmartLockInfo."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: TuyaBLECoordinator,
        device: TuyaBLEDevice,
        product: TuyaBLEProductInfo,
    ) -> None:
        assert product.smartlock is not None
        self._lock_info: TuyaBLESmartLockInfo = product.smartlock

        features = LockEntityFeature(0)
        if self._lock_info.open_dp is not None:
            features |= LockEntityFeature.OPEN

        super().__init__(
            hass,
            coordinator,
            device,
            product,
            LockEntityDescription(key="lock", name=product.name),
        )
        self._attr_supported_features = features

    @property
    def is_locked(self) -> bool | None:
        """Return True when the bolt is thrown (locked)."""
        dp = self._device.datapoints.get(self._lock_info.lock_state_dp)
        if dp is None:
            return None
        return bool(dp.value)

    @property
    def is_locking(self) -> bool | None:
        return None

    @property
    def is_unlocking(self) -> bool | None:
        return None

    async def async_lock(self, **kwargs: Any) -> None:
        """Send the lock command."""
        await self._set_lock_dp(True)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Send the unlock command."""
        await self._set_lock_dp(False)

    async def async_open(self, **kwargs: Any) -> None:
        """Trigger a momentary open / electric-strike release."""
        if self._lock_info.open_dp is None:
            _LOGGER.warning(
                "%s: async_open called but no open_dp configured", self.name
            )
            return
        dp = self._device.datapoints.get_or_create(
            self._lock_info.open_dp, TuyaBLEDataPointType.DT_BOOL, True
        )
        if dp is not None:
            await dp.set_value(True)

    async def _set_lock_dp(self, locked: bool) -> None:
        """Write a boolean value to the action DP."""
        dp = self._device.datapoints.get_or_create(
            self._lock_info.lock_action_dp,
            TuyaBLEDataPointType.DT_BOOL,
            locked,
        )
        if dp is not None:
            await dp.set_value(locked)
        else:
            _LOGGER.error(
                "%s: could not get/create DP %d for lock command",
                self.name,
                self._lock_info.lock_action_dp,
            )
