"""Config and options flow: connect, scan the panel, code settings."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers import selector
import voluptuous as vol

from . import TelenotConfigEntry, const, wait_available
from .client import Detector, TelenotClient
from .const import (
    CODE_MODES,
    CONF_CODE,
    CONF_CODE_FOR,
    CONF_DETECTORS,
    DEFAULT_CODE_FOR,
    DEFAULT_PORT,
    DOMAIN,
    MODEL,
)
from .inventory import to_storage

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(int, vol.Range(min=1, max=65535)),
    }
)


class TelenotConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._client: TelenotClient | None = None
        self._scan_task: asyncio.Task[list[Detector]] | None = None
        self._detectors: list[dict] = []

    @staticmethod
    @callback
    def async_get_options_flow(entry: TelenotConfigEntry) -> TelenotOptionsFlow:
        return TelenotOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port = user_input[CONF_HOST].strip(), user_input[CONF_PORT]
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            client = TelenotClient(host, port, const.CLIENT_TIMING)
            client.start()
            if await wait_available(client, const.CONNECT_WAIT):
                self._client = client
                self._data = {CONF_HOST: host, CONF_PORT: port}
                return await self.async_step_scan_choice()
            await client.stop()
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_scan_choice(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="scan_choice", menu_options=["scan", "skip_scan"])

    async def async_step_skip_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._finish()

    async def async_step_scan(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._client is not None
        if self._scan_task is None:
            self._scan_task = self.hass.async_create_task(
                self._client.scan(self.async_update_progress)
            )
        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="scan", progress_action="scan", progress_task=self._scan_task
            )
        try:
            self._detectors = to_storage(self._scan_task.result())
        except ConnectionError:
            return self.async_show_progress_done(next_step_id="scan_failed")
        return self.async_show_progress_done(next_step_id="scan_done")

    async def async_step_scan_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._finish()

    async def async_step_scan_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self._stop_client()
        return self.async_abort(reason="scan_failed")

    async def _finish(self) -> ConfigFlowResult:
        await self._stop_client()
        return self.async_create_entry(
            title=f"Telenot {MODEL}",
            data={**self._data, CONF_DETECTORS: self._detectors},
            options={CONF_CODE: "", CONF_CODE_FOR: DEFAULT_CODE_FOR},
        )

    async def _stop_client(self) -> None:
        if self._client is not None:
            await self._client.stop()
            self._client = None

    @callback
    def async_remove(self) -> None:
        """Flow aborted by the user: free the converter's single connection."""
        if self._scan_task is not None and not self._scan_task.done():
            self._scan_task.cancel()
        if self._client is not None:
            self.hass.async_create_task(self._client.stop())
            self._client = None


class TelenotOptionsFlow(OptionsFlowWithReload):
    """Code settings (reload on change) and a new scan over the running connection."""

    def __init__(self) -> None:
        self._scan_task: asyncio.Task[list[Detector]] | None = None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["code", "rescan"])

    async def async_step_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_CODE: user_input.get(CONF_CODE, "").strip(),
                    CONF_CODE_FOR: user_input.get(CONF_CODE_FOR, []),
                }
            )
        schema = vol.Schema(
            {
                vol.Optional(CONF_CODE): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Optional(CONF_CODE_FOR): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=CODE_MODES,
                        multiple=True,
                        translation_key="code_for",
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="code",
            data_schema=self.add_suggested_values_to_schema(
                schema,
                {
                    CONF_CODE: self.config_entry.options.get(CONF_CODE, ""),
                    CONF_CODE_FOR: self.config_entry.options.get(CONF_CODE_FOR, DEFAULT_CODE_FOR),
                },
            ),
        )

    async def async_step_rescan(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        client = self.config_entry.runtime_data.client
        if self._scan_task is None:
            self._scan_task = self.hass.async_create_task(client.scan(self.async_update_progress))
        if not self._scan_task.done():
            return self.async_show_progress(
                step_id="rescan", progress_action="scan", progress_task=self._scan_task
            )
        try:
            detectors = to_storage(self._scan_task.result())
        except ConnectionError:
            return self.async_show_progress_done(next_step_id="rescan_failed")
        self.hass.config_entries.async_update_entry(
            self.config_entry, data={**self.config_entry.data, CONF_DETECTORS: detectors}
        )
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return self.async_show_progress_done(next_step_id="rescan_done")

    async def async_step_rescan_done(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_create_entry(data=dict(self.config_entry.options))

    async def async_step_rescan_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_abort(reason="scan_failed")
