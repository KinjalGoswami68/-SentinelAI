
# drift_detector.py
import warnings
warnings.filterwarnings("ignore")


class CUSUMDetector:

    def __init__(self, target=8.0, threshold=3.0, drift=0.5):
        """
        target    - expected normal quality score
                    Default 8.0 — we expect AI to score around 8
        threshold - total accumulated drift that fires alert
                    Default 3.0 — fires when 3 points accumulated
        drift     - minimum drop size counted as meaningful
                    Default 0.5 — drops below 0.5 are ignored
                    as normal variation
        """
        if not 0 < target <= 10:
            raise ValueError(f"target must be 0-10. Got {target}")

        if threshold <= 0:
            raise ValueError(
                f"threshold must be positive. Got {threshold}"
            )

        if drift < 0:
            raise ValueError(
                f"drift must be non-negative. Got {drift}"
            )

        self.target         = float(target)
        self.threshold      = float(threshold)
        self.drift          = float(drift)
        self.cumulative_sum = 0.0
        self.alert_count    = 0

    def update(self, quality_score):
        """
        Input:
            quality_score — float between 0.0 and 10.0

        Output:
            dict with:
                alert           - True if drift threshold crossed
                cumulative_sum  - accumulated drift before reset
                quality_score   - the input score
                message         - human readable description
                total_alerts    - how many alerts fired so far
        """
        if quality_score is None:
            raise ValueError("quality_score cannot be None")

        quality_score = float(quality_score)

        if not 0.0 <= quality_score <= 10.0:
            raise ValueError(
                f"quality_score must be 0-10. Got {quality_score}"
            )

        drop = self.target - quality_score

        meaningful_drop = drop - self.drift

        
        self.cumulative_sum = max(
            0.0,
            self.cumulative_sum + meaningful_drop
        )

        
        alert = self.cumulative_sum >= self.threshold

        
        sum_at_alert = round(self.cumulative_sum, 2)

        if alert:
            self.alert_count += 1
            message = (
                f"Drift alert fired. "
                f"Accumulated drift: {sum_at_alert}. "
                f"Triggered at score: {quality_score}. "
                f"Expected score: {self.target}."
            )
            
            self.cumulative_sum = 0.0
        else:
            message = (
                f"No drift alert. "
                f"Current accumulated drift: {sum_at_alert}. "
                f"Threshold: {self.threshold}."
            )

        return {
            "alert"        : alert,
            "cumulative_sum": sum_at_alert,
            "quality_score" : quality_score,
            "message"       : message,
            "total_alerts"  : self.alert_count
        }

    def status(self):
        """Return current detector state for debugging."""
        return {
            "target"        : self.target,
            "threshold"     : self.threshold,
            "drift"         : self.drift,
            "cumulative_sum": round(self.cumulative_sum, 2),
            "alert_count"   : self.alert_count
        }

    def reset(self):
        """Reset accumulated drift to zero."""
        self.cumulative_sum = 0.0