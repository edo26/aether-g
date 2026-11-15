import pytest

from app.detector import ANOMALY, DUST_REJECTED, NO_EVENT, NOMINAL, Detector
from tests.helpers import samples, simulate


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def run(scenario, n, det=None):
    det = det or Detector(clock=Clock())
    return [det.process(s) for s in samples(scenario, n)]


@pytest.mark.parametrize("scenario", ["nominal", "airgapped"])
def test_nominal_and_airgapped_always_nominal(scenario):
    for r in run(scenario, 400):
        assert r.status == NOMINAL
        assert r.confidence_score == 0.95
        assert r.false_alarm_dust_check == NO_EVENT


def test_dust_is_nominal_with_marker_from_the_first_sample():
    results = run("dust", 400)
    for r in results:
        assert r.status == NOMINAL
        assert r.confidence_score == 0.95
        assert r.false_alarm_dust_check == DUST_REJECTED
    assert not any(r.status_changed for r in results)


def test_smoldering_trips_at_10_5_s_and_stays_latched():
    results = run("smoldering", 260)  # 130 s: past the 60 s plateau
    sims = simulate("smoldering", 260)
    first = next(i for i, r in enumerate(results) if r.status == ANOMALY)
    assert sims[first]["timestamp_ms"] == 10500
    assert sims[first]["telemetry"]["co_ppm"] == pytest.approx(26.0, abs=0.01)
    assert results[first].confidence_score == pytest.approx(0.604, abs=0.002)
    assert results[first].status_changed and results[first].previous_status == NOMINAL
    assert all(r.status == ANOMALY for r in results[first:])
    assert sum(r.status_changed for r in results) == 1
    assert results[-1].confidence_score == pytest.approx(0.95, abs=0.001)
    assert all(r.false_alarm_dust_check == NO_EVENT for r in results)
    confs = [r.confidence_score for r in results[first:]]
    assert confs == sorted(confs)  # rises monotonically 0.6 -> 0.95


@pytest.mark.parametrize("scenario", ["nominal", "dust", "smoldering", "airgapped"])
def test_matches_the_simulators_embedded_detector_sample_by_sample(scenario):
    results = run(scenario, 260)
    for r, sim in zip(results, simulate(scenario, 260)):
        assert r.stage1() == sim["stage1_detector"]


def test_incoming_stage1_detector_is_ignored():
    det = Detector(clock=Clock())
    for s in samples("nominal", 20):
        s.stage1_detector = None
        assert det.process(s).status == NOMINAL
    liar = simulate("nominal", 1)[0]
    liar["stage1_detector"]["status"] = "SMOLDERING_ANOMALY"
    from app.contract import Sample

    assert det.process(Sample.model_validate({**liar, "timestamp_ms": 99999})).status == NOMINAL


def test_anomaly_takes_precedence_over_dust():
    det = Detector(clock=Clock())
    nominal = samples("nominal", 12)
    smold = samples("smoldering", 40)
    for s in nominal:
        det.process(s)
    # high PM2.5 plus rising CO: must be an anomaly, dust marker stays NOMINAL_NO_EVENT
    base_ts = nominal[-1].timestamp_ms
    last = None
    for i, s in enumerate(smold):
        s.timestamp_ms = base_ts + 500 * (i + 1)
        s.telemetry.pm25_ug_m3 = 140.0
        last = det.process(s)
    assert last.status == ANOMALY and last.false_alarm_dust_check == NO_EVENT


def test_dust_not_rejected_when_temperature_rises():
    det = Detector(clock=Clock())
    out = None
    for i, s in enumerate(samples("dust", 30)):
        s.telemetry.max_ir_temp_c = 24.2 + 0.5 * i  # steady heating, no CO
        out = det.process(s)
    assert out.status == NOMINAL and out.false_alarm_dust_check == NO_EVENT


class TestSessionReset:
    def trip(self, det):
        for s in samples("smoldering", 40):
            det.process(s)
        assert det.status_of("AVIONICS_BAY_B2") == ANOMALY

    def test_timestamp_backwards(self):
        det = Detector(clock=Clock())
        self.trip(det)
        r = det.process(samples("nominal", 1)[0])  # t=0 again
        assert r.reset_reason == "timestamp_backwards"
        assert r.status == NOMINAL and r.previous_status == ANOMALY and r.status_changed

    def test_mode_change(self):
        det = Detector(clock=Clock())
        self.trip(det)
        s = samples("airgapped", 41)[-1]  # NASA_REPLAY, later timestamp than the live run
        r = det.process(s)
        assert r.reset_reason == "mode_change" and r.status == NOMINAL

    def test_idle_timeout(self):
        clock = Clock()
        det = Detector(clock=clock)
        self.trip(det)
        clock.t += 31.0
        s = samples("nominal", 1)[0]
        s.timestamp_ms = 99999  # forward in time, so only the idle rule can fire
        assert det.process(s).reset_reason == "idle_timeout"

    def test_no_reset_inside_the_idle_window(self):
        clock = Clock()
        det = Detector(clock=clock)
        self.trip(det)
        clock.t += 29.0
        s = samples("smoldering", 80)[-1]
        assert det.process(s).reset_reason is None
        assert det.status_of("AVIONICS_BAY_B2") == ANOMALY


def test_compartments_are_independent():
    det = Detector(clock=Clock())
    for s in samples("smoldering", 40):
        det.process(s)
    other = samples("nominal", 1)[0]
    other.compartment_id = "CARGO_BAY_A1"
    assert det.process(other).status == NOMINAL
    assert det.status_of("AVIONICS_BAY_B2") == ANOMALY


def test_features_are_reported():
    results = run("smoldering", 60)
    f = results[-1].features
    assert f.voc_delta > 0 and f.temp_delta > 0
    assert f.ratio is not None and f.ratio > 0
    nominal_f = run("nominal", 30)[-1].features
    assert nominal_f.ratio is None  # dCO2 below the 20 ppm noise floor is skipped (usually)
