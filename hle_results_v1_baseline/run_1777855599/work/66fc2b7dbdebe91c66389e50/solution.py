def calculate_maintenance_daily(weight):
    # Holliday-Segar Method (Standard for maintenance fluids)
    if weight <= 10:
        return weight * 100
    elif weight <= 20:
        return 1000 + (weight - 10) * 50
    else:
        return 1500 + (weight - 20) * 20

weight = 25
daily_total = calculate_maintenance_daily(weight)
hourly_rate = daily_total / 24
print(f"Weight: {weight}kg")
print(f"Daily maintenance: {daily_total} cc")
print(f"Hourly maintenance: {hourly_rate} cc/hr")
