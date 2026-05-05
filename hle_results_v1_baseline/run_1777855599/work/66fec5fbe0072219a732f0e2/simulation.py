import random
import math

def simulate(n1, m1, n2, m2):
    # Initial positions
    x = [0, n1, n1 + m1, n1 + m1 + n2, n1 + m1 + n2 + m2]
    rate = 1.0
    time = 0.0
    
    # First collision
    while True:
        total_rate = len(x) * rate
        dt = random.expovariate(total_rate)
        time += dt
        
        idx = random.randint(0, len(x) - 1)
        direction = 1 if random.random() < 0.5 else -1
        x[idx] += direction
        
        x.sort()
        
        collision_idx = -1
        for i in range(len(x) - 1):
            if x[i] == x[i+1]:
                collision_idx = i
                break
        
        if collision_idx != -1:
            x.pop(collision_idx + 1)
            x.pop(collision_idx)
            rate = 2.0
            break
            
    # Second collision
    while len(x) > 1:
        total_rate = len(x) * rate
        dt = random.expovariate(total_rate)
        time += dt
        
        idx = random.randint(0, len(x) - 1)
        direction = 1 if random.random() < 0.5 else -1
        x[idx] += direction
        x.sort()
        
        collision_idx = -1
        for i in range(len(x) - 1):
            if x[i] == x[i+1]:
                collision_idx = i
                break
        
        if collision_idx != -1:
            x.pop(collision_idx + 1)
            x.pop(collision_idx)
            if len(x) == 1:
                return time
            
    return time

def run_simulations(n1, m1, n2, m2, num_sims=20000):
    total_time = 0.0
    for _ in range(num_sims):
        total_time += simulate(n1, m1, n2, m2)
    return total_time / num_sims

if __name__ == "__main__":
    print(f"Simulating (1,1,1,1): {run_simulations(1, 1, 1, 1)}")
    print(f"Simulating (1,2,1,2): {run_simulations(1, 2, 1, 2)}")
    print(f"Simulating (2,1,2,1): {run_simulations(2, 1, 2, 1)}")
    print(f"Simulating (1,1,2,2): {run_simulations(1, 1, 2, 2)}")
    print(f"Simulating (2,2,1,1): {run_simulations(2, 2, 1, 1)}")
    print(f"Simulating (1,1,1,2): {run_simulations(1, 1, 1, 2)}")
    print(f"Simulating (2,1,1,1): {run_simulations(2, 1, 1, 1)}")
