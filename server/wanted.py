"""
PitWall - the telemetry variables we actually read.

iRacing publishes around 300 variables. Unpacking all of them 60 times a second
costs real CPU on the machine that is also running the sim, and PitWall uses
maybe a third of them.

This is that third. Anything not listed is simply not read. Names that do not
exist in a given car or session are skipped silently, so it is safe to list
variables that only some cars publish.

If you add a field to the engine, add its variable here or it will be None.
"""

WANTED = frozenset(
    [
        # --- session / race control -------------------------------------
        "SessionTime", "SessionTick", "SessionNum", "SessionState",
        "SessionUniqueID", "SessionFlags", "SessionTimeRemain",
        "SessionTimeTotal", "SessionLapsRemain", "SessionLapsRemainEx",
        "SessionLapsTotal", "SessionTimeOfDay", "SessionJokerLapsRemain",
        "SessionOnJokerLap", "RaceLaps", "PitsOpen", "PaceMode",

        # --- per-car arrays: everything the timing tower is built on ----
        "CarIdxLap", "CarIdxLapCompleted", "CarIdxLapDistPct",
        "CarIdxPosition", "CarIdxClassPosition", "CarIdxClass",
        "CarIdxOnPitRoad", "CarIdxTrackSurface", "CarIdxTrackSurfaceMaterial",
        "CarIdxF2Time", "CarIdxEstTime", "CarIdxLastLapTime",
        "CarIdxBestLapTime", "CarIdxBestLapNum", "CarIdxTireCompound",
        "CarIdxQualTireCompound", "CarIdxQualTireCompoundLocked",
        "CarIdxFastRepairsUsed", "CarIdxPaceLine", "CarIdxPaceRow",
        "CarIdxPaceFlags", "CarIdxP2P_Status", "CarIdxP2P_Count",
        "CarIdxSteer", "CarIdxRPM", "CarIdxGear",

        # --- the player's car -------------------------------------------
        "PlayerCarIdx", "PlayerCarPosition", "PlayerCarClassPosition",
        "PlayerCarClass", "PlayerTrackSurface", "PlayerCarInPitStall",
        "PlayerCarPitSvStatus", "PlayerCarMyIncidentCount",
        "PlayerCarTeamIncidentCount", "PlayerCarDriverIncidentCount",
        "PlayerCarTowTime", "PlayerCarWeightPenalty", "PlayerTireCompound",
        "PlayerFastRepairsUsed", "PlayerCarDryTireSetLimit",

        # --- lap and delta ----------------------------------------------
        "Lap", "LapCompleted", "LapDist", "LapDistPct",
        "LapCurrentLapTime", "LapLastLapTime", "LapBestLapTime", "LapBestLap",
        "LapDeltaToBestLap", "LapDeltaToBestLap_OK",
        "LapDeltaToOptimalLap", "LapDeltaToOptimalLap_OK",
        "LapDeltaToSessionBestLap", "LapDeltaToSessionBestLap_OK",
        "LapDeltaToSessionOptimalLap", "LapDeltaToSessionLastlLap",

        # --- inputs and drivetrain (the inputs trace) -------------------
        "Throttle", "Brake", "Clutch", "ThrottleRaw", "BrakeRaw",
        "HandBrake", "Gear", "RPM", "Speed", "SteeringWheelAngle",
        "SteeringWheelAngleMax", "SteeringWheelTorque", "BrakeABSactive",
        "ShiftIndicatorPct", "ShiftPowerPct", "OnPitRoad", "PushToPass",
        "CarLeftRight", "DriverMarker",

        # --- fuel and engine --------------------------------------------
        "FuelLevel", "FuelLevelPct", "FuelUsePerHour", "FuelPress",
        "OilTemp", "OilPress", "WaterTemp", "Voltage", "EngineWarnings",
        "EnergyERSBatteryPct",

        # --- pit service -------------------------------------------------
        "PitSvFlags", "PitSvFuel", "PitSvTireCompound", "PitstopActive",
        "PitRepairLeft", "PitOptRepairLeft", "FastRepairUsed",
        "FastRepairAvailable",
        "PitSvLFP", "PitSvRFP", "PitSvLRP", "PitSvRRP",

        # --- tyres (player only; iRacing does not publish these for
        #     opponents, and only refreshes them on inspection) ----------
        "LFcoldPressure", "RFcoldPressure", "LRcoldPressure", "RRcoldPressure",
        "LFtempCL", "LFtempCM", "LFtempCR",
        "RFtempCL", "RFtempCM", "RFtempCR",
        "LRtempCL", "LRtempCM", "LRtempCR",
        "RRtempCL", "RRtempCM", "RRtempCR",
        "LFwearL", "LFwearM", "LFwearR",
        "RFwearL", "RFwearM", "RFwearR",
        "LRwearL", "LRwearM", "LRwearR",
        "RRwearL", "RRwearM", "RRwearR",
        "TireSetsAvailable", "TireSetsUsed",

        # --- weather ------------------------------------------------------
        "AirTemp", "AirDensity", "AirPressure", "TrackTemp", "TrackTempCrew",
        "TrackWetness", "Precipitation", "WeatherDeclaredWet",
        "RelativeHumidity", "FogLevel", "WindVel", "WindDir", "Skies",
        "SolarAltitude",

        # --- broadcast: camera, radio, replay -----------------------------
        "CamCarIdx", "CamCameraNumber", "CamGroupNumber", "CamCameraState",
        "RadioTransmitCarIdx", "RadioTransmitRadioIdx",
        "RadioTransmitFrequencyIdx",
        "IsReplayPlaying", "ReplayFrameNum", "ReplayFrameNumEnd",
        "ReplayPlaySpeed", "ReplayPlaySlowMotion", "ReplaySessionTime",
        "ReplaySessionNum",

        # --- state and position (position feeds the self-building map) ----
        "IsOnTrack", "IsOnTrackCar", "IsInGarage", "DisplayUnits",
        "Lat", "Lon", "Alt", "Yaw", "YawNorth", "VelocityX", "VelocityY",

        # --- team racing ---------------------------------------------------
        "DCLapStatus", "DCDriversSoFar",

        # --- performance (shown in diagnostics) ----------------------------
        "FrameRate", "CpuUsageFG", "GpuUsage", "ChanLatency", "ChanQuality",
    ]
)
