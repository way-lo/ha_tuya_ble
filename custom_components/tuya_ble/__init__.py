"""The Tuya BLE integration."""

from __future__ import annotations

import asyncio
import logging

from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS as BLEAK_EXCEPTIONS, get_device

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import ADDRESS, BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady

from .tuya_ble import TuyaBLEDevice

from .cloud import HASSTuyaBLEDeviceManager
from .const import DOMAIN
from .devices import TuyaBLECoordinator, TuyaBLEData, get_device_product_info

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.LIGHT,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.COVER,
    Platform.EVENT,
    Platform.VACUUM,
]

_LOGGER = logging.getLogger(__name__)

# How long to wait for the BLE adapter to be ready after HA starts (seconds)
BLE_ADAPTER_READY_TIMEOUT = 10
# How long to wait between update retry attempts (seconds)
BLE_UPDATE_RETRY_DELAY = 5
# How many times to retry the initial update before giving up
BLE_UPDATE_RETRY_COUNT = 3


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tuya BLE from a config entry."""

    address: str = entry.data[CONF_ADDRESS]

    # Wait for the BLE adapter to be ready before attempting to find the device.
    # This prevents silent failures when the integration loads before the
    # Bluetooth stack has fully initialised after a reboot.
    if not bluetooth.async_scanner_count(hass, connectable=True):
        try:
            await bluetooth.async_process_advertisements(
                hass,
                lambda service_info, _: True,
                BluetoothCallbackMatcher({ADDRESS: address}),
                bluetooth.BluetoothScanningMode.ACTIVE,
                BLE_ADAPTER_READY_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _LOGGER.debug(
                "BLE adapter not ready within %ss, proceeding anyway",
                BLE_ADAPTER_READY_TIMEOUT,
            )

    ble_device = bluetooth.async_ble_device_from_address(
        hass, address.upper(), True
    ) or await get_device(address)
    if not ble_device:
        raise ConfigEntryNotReady(
            f"Could not find Tuya BLE device with address {address}"
        )

    manager = HASSTuyaBLEDeviceManager(hass, entry.options.copy())
    device = TuyaBLEDevice(manager, ble_device)
    await device.initialize()
    product_info = get_device_product_info(device)

    coordinator = TuyaBLECoordinator(hass, device)

    last_exc: Exception | None = None
    for attempt in range(1, BLE_UPDATE_RETRY_COUNT + 1):
        try:
            await device.update()
            last_exc = None
            break
        except BLEAK_EXCEPTIONS as ex:
            last_exc = ex
            _LOGGER.debug(
                "%s: Initial update attempt %d/%d failed: %s",
                address,
                attempt,
                BLE_UPDATE_RETRY_COUNT,
                ex,
            )
            if attempt < BLE_UPDATE_RETRY_COUNT:
                await asyncio.sleep(BLE_UPDATE_RETRY_DELAY)

    if last_exc is not None:
        raise ConfigEntryNotReady(
            f"Could not communicate with Tuya BLE device with address {address}"
        ) from last_exc

    @callback
    def _async_update_ble(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Update from a ble callback."""
        device.set_ble_device_and_advertisement_data(
            service_info.device, service_info.advertisement
        )

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_update_ble,
            BluetoothCallbackMatcher({ADDRESS: address}),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = TuyaBLEData(
        entry.title,
        device,
        product_info,
        manager,
        coordinator,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    async def _async_stop(event: Event) -> None:
        """Close the connection."""
        await device.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    data: TuyaBLEData = hass.data[DOMAIN][entry.entry_id]
    if entry.title != data.title:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        data: TuyaBLEData = hass.data[DOMAIN].pop(entry.entry_id)
        await data.device.stop()

    return unload_ok
