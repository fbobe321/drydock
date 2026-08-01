#!/usr/bin/env bash
# selfdistill_launch.sh — single-instance launcher. Holds loop.lock for the loop's whole
# life via `flock -n`, so a second launch (watchdog race, double @reboot) simply exits.
SD=/data3/tbench_local/frontier/selfdistill
exec flock -n "$SD/loop.lock" bash "$SD/selfdistill.sh" loop
