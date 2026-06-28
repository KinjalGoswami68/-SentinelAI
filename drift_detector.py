
# SentinelAI 

import warnings
warnings.filterwarnings("ignore")

# THE CUSUM FUNCTION
#
# scores    = list of quality scores over time
#             example: [9.0, 8.5, 8.0, 7.5, 7.0]
#
# target    = what score we expect normally
#             we use 8.0 as the expected good score
#
# threshold = how much total drift triggers alert
#             we use 3.0
#             meaning if quality drifted 3 points
#             total - fire the alert
#
# drift     = how sensitive we are to each drop
#             we use 0.5
#             meaning drops smaller than 0.5
#             are ignored as natural variation

def cusum_detect(scores, target=8.0, threshold=3.0, drift=0.5):

    
    cumulative_sum = 0.0
    alerts = []

    
    alert_messages = []
    for i in range(len(scores)):

        current_score = scores[i]

        # Calculate how much this score dropped
        drop = target - current_score

        
        meaningful_drop = drop - drift

        cumulative_sum = max(0.0, cumulative_sum + meaningful_drop)

        if cumulative_sum >= threshold:

            # Alert fires here
            alerts.append(i)
            alert_messages.append(
                f"Alert at score {i + 1}: "
                f"score={current_score}, "
                f"accumulated_drift={round(cumulative_sum, 2)}"
            )

            cumulative_sum = 0.0

    return alerts, alert_messages, cumulative_sum



# TEST SCENARIO 1

print("=" * 55)
print("TEST 1 - GRADUAL QUALITY DROP OVER 10 DAYS")
print("=" * 55)
print()

gradual_drop_scores = [
    9.0,   # Day 1  - excellent quality
    8.8,   # Day 2  - excellent quality
    8.5,   # Day 3  - good quality
    8.2,   # Day 4  - good quality
    7.8,   # Day 5  - quality dropping
    7.5,   # Day 6  - quality dropping
    7.0,   # Day 7  - poor quality
    6.8,   # Day 8  - poor quality
    6.5,   # Day 9  - very poor quality
    6.0,   # Day 10 - very poor quality
]

print("Scores over 10 days:")
for i in range(len(gradual_drop_scores)):
    score = gradual_drop_scores[i]
    print(f"  Day {i + 1:2d}  |  Score: {score} ")
print()

alerts, messages, final_sum = cusum_detect(gradual_drop_scores)

if len(alerts) > 0:
    print(f"CUSUM fired {len(alerts)} alert(s):")
    for msg in messages:
        print(f"  ALERT: {msg}")
    print()
    print("Result: CUSUM correctly detected gradual drop")
else:
    print("Result: No alerts fired")
print()

# TEST SCENARIO 2

print("=" * 55)
print("TEST 2 - STABLE GOOD QUALITY OVER 10 DAYS")
print("=" * 55)
print()

stable_scores = [
    9.0,   # Day 1
    8.8,   # Day 2
    9.1,   # Day 3
    8.9,   # Day 4
    9.0,   # Day 5
    8.7,   # Day 6
    9.2,   # Day 7
    8.8,   # Day 8
    9.0,   # Day 9
    8.9,   # Day 10
]

print("Scores over 10 days:")
for i in range(len(stable_scores)):
    score = stable_scores[i]
    print(f"  Day {i + 1:2d}  |  Score: {score} ")
print()

alerts, messages, final_sum = cusum_detect(stable_scores)

if len(alerts) > 0:
    print(f"CUSUM fired {len(alerts)} alert(s):")
    for msg in messages:
        print(f"  ALERT: {msg}")
    print()
    print("Result: False positive - stable AI wrongly flagged")
else:
    print("No alerts fired.")
    print("Result: CUSUM correctly ignored stable good quality")
print()



# TEST SCENARIO 3

print("=" * 55)
print("TEST 3 - SUDDEN CRASH THEN RECOVERY")
print("=" * 55)
print()

crash_scores = [
    9.0,   # Day 1  - good
    9.0,   # Day 2  - good
    9.0,   # Day 3  - good
    4.0,   # Day 4  - sudden crash
    4.0,   # Day 5  - still crashed
    9.0,   # Day 6  - recovered
    9.0,   # Day 7  - recovered
    9.0,   # Day 8  - recovered
    9.0,   # Day 9  - recovered
    9.0,   # Day 10 - recovered
]

print("Scores over 10 days:")
for i in range(len(crash_scores)):
    score = crash_scores[i]
    print(f"  Day {i + 1:2d}  |  Score: {score} ")
print()

alerts, messages, final_sum = cusum_detect(crash_scores)

if len(alerts) > 0:
    print(f"CUSUM fired {len(alerts)} alert(s):")
    for msg in messages:
        print(f"  ALERT: {msg}")
    print()
    print("Result: CUSUM correctly detected the crash")
else:
    print("No alerts fired.")
    print("Result: CUSUM missed the crash")
print()


# TEST SCENARIO 4

print("=" * 55)
print("TEST 4 - REALISTIC PRODUCTION SIMULATION")
print("=" * 55)
print()

realistic_scores = [
    # Week 1 - AI working normally
    9.1, 8.9, 9.0, 8.8, 9.2, 8.9, 9.0,
    # Week 2 - AI slowly degrading
    8.5, 8.2, 7.9, 7.6, 7.3, 7.0, 6.8,
    # Week 3 - AI clearly broken
    6.5, 6.2, 5.9, 5.7, 5.5, 5.2, 5.0,
]

print("Scores over 21 days (3 weeks):")
for i in range(len(realistic_scores)):
    score = realistic_scores[i]
    week = (i // 7) + 1
    day = (i % 7) + 1
    print(f"  Week {week} Day {day}  |  Score: {score} ")
print()

alerts, messages, final_sum = cusum_detect(realistic_scores)

if len(alerts) > 0:
    print(f"CUSUM fired {len(alerts)} alert(s):")
    for msg in messages:
        print(f"  ALERT: {msg}")
    print()
    print("Result: CUSUM correctly detected production degradation")
else:
    print("No alerts fired")
print()



# FINAL SUMMARY

print("=" * 55)
print("CUSUM ALGORITHM - SUMMARY")
print("=" * 55)
print()
print("Test 1 - Gradual drop    : should fire alert")
print("Test 2 - Stable quality  : should NOT fire alert")
print("Test 3 - Sudden crash    : should fire alert")
print("Test 4 - Realistic data  : should fire alert")
print()
print("CUSUM works by accumulating small quality drops.")
print("Natural variation is ignored.")
print("Only real sustained drops trigger an alert.")
print()
print("Key parameters used:")
print("  target    = 8.0  - expected normal quality score")
print("  threshold = 3.0  - total drift that triggers alert")
print("  drift     = 0.5  - minimum drop size to count")
print()
print("=" * 55)
print("drift_detector.py complete")
print("Next file: database.py")
print("=" * 55)