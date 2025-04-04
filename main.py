import os
import time
import decky_plugin
import asyncio
from pathlib import Path
import sqlite3
from collections import defaultdict


def split_by_app(data):
    idxs = []
    start_idx = 0
    app = data[0][-1]
    for idx, d in enumerate(data):
        if d[4] != app:
            idxs.append((start_idx, idx - 1, app))
            start_idx = idx
            app = d[-1]
    if start_idx != idx:
        idxs.append((start_idx, idx, app))
    return idxs


def get_battery_device():
    """Find the first available battery device in /sys/class/power_supply/"""
    power_supply_path = "/sys/class/power_supply/"
    for device in os.listdir(power_supply_path):
        device_path = os.path.join(power_supply_path, device)
        if os.path.isdir(device_path):
            # Check if it's a battery by looking for capacity file
            capacity_file = os.path.join(device_path, "capacity")
            if os.path.exists(capacity_file):
                return device
    return None


class Plugin:
    async def _main(self):
        try:
            self.app = "Unknown"
            decky_plugin.logger.info("steam deck battery logger _main")
            battery_db = Path(decky_plugin.DECKY_PLUGIN_RUNTIME_DIR) / "battery.db"
            database_file = str(battery_db)
            self.con = sqlite3.connect(database_file)
            self.cursor = self.con.cursor()
            tables = self.cursor.execute(
                "select name from sqlite_master where type='table';"
            ).fetchall()
            if not tables:
                decky_plugin.logger.info("Creating database table for the first time")
                self.cursor.execute(
                    "create table battery (time __integer, capacity __integer, status __integer, power __integer, app __text);"
                )
                self.con.commit()

            # Find battery device
            self.battery_device = get_battery_device()
            if not self.battery_device:
                raise Exception("No battery device found")

            loop = asyncio.get_event_loop()
            self._recorder_task = loop.create_task(Plugin.recorder(self))
            decky_plugin.logger.info("steam deck battery logger _main finished")
        except Exception:
            decky_plugin.logger.exception("_main")

    async def _unload(self):
        decky_plugin.logger.info("steam deck battery logger _unload")
        pass

    async def set_app(self, app: str = "Unknown"):
        decky_plugin.logger.info(f"Getting app as {app}")
        if app:
            self.app = app
        return True

    async def get_recent_data(self, lookback=2):
        try:
            decky_plugin.logger.info(f"lookback {lookback}")
            end_time = time.time()
            start_time = end_time - 24 * lookback * 3600
            data = self.cursor.execute(
                "select * from battery where time > " + str(int(start_time))
            ).fetchall()
            diff = end_time - start_time
            x_axis = [(d[0] - start_time) / diff for d in data]
            y_axis = [d[1] / 100 for d in data]
            per_app_powers = defaultdict(list)
            for start, end, app in split_by_app(data):
                if app == "Unknown":
                    app = "Steam"
                per_app_powers[app].extend(
                    [d[3] / 10.0 for d in data[start:end] if d[2] == -1]
                )
            per_app_data = [
                {"name": app, "average_power": int(sum(power_data) / len(power_data))}
                for app, power_data in per_app_powers.items()
                if power_data
            ]
            return {
                "x": x_axis,
                "cap": y_axis,
                "power_data": sorted(per_app_data, key=lambda x: -x["average_power"]),
            }
        except Exception:
            decky_plugin.logger.exception("could not get recent data")

    async def recorder(self):
        power_supply_path = f"/sys/class/power_supply/{self.battery_device}/"
        volt_file = open(os.path.join(power_supply_path, "voltage_now"))
        curr_file = open(os.path.join(power_supply_path, "current_now"))
        cap_file = open(os.path.join(power_supply_path, "capacity"))
        status = open(os.path.join(power_supply_path, "status"))
        logger = decky_plugin.logger

        logger.info(f"recorder started using battery device {self.battery_device}")
        running_list = []
        while True:
            try:
                volt_file.seek(0)
                curr_file.seek(0)
                cap_file.seek(0)
                status.seek(0)
                volt = int(volt_file.read().strip())
                curr = int(curr_file.read().strip())
                cap = int(cap_file.read().strip())
                stat = status.read().strip()
                if stat == "Discharging":
                    stat = -1
                elif stat == "Charging":
                    stat = 1
                else:
                    stat = 0

                power = int(volt * curr * 10.0**-11)
                curr_time = int(time.time())
                running_list.append((curr_time, cap, stat, power, self.app))
                if len(running_list) > 10:
                    self.cursor.executemany(
                        "insert into battery values (?, ?, ?, ?, ?)", running_list
                    )
                    self.con.commit()
                    running_list = []
            except Exception:
                logger.exception("recorder")
            await asyncio.sleep(5)
