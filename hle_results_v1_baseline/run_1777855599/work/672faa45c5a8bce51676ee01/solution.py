import random

def simulate(n, trials=10000):
    total_time = 0
    total_count_special = 0
    
    for _ in range(trials):
        p1 = 0
        p2 = 1
        time = 0
        special_count = 0
        
        while True:
            time += 1
            # Each gift moves to a neighbor with 0.5 probability
            # This is equivalent to: p1_next = p1 + 1 or p1 - 1
            # p2_next = p2 + 1 or p2 - 1
            # But the problem says "the people who has a gift is giving it to each of his neighbor by 1/2 probability"
            # This could mean:
            # For each neighbor, prob 1/2.
            # Let's try the "always gives it away" interpretation:
            # p1_next = (p1 + 1) % n if random.random() < 0.5 else (p1 - 1) % n
            # p2_next = (p2 + 1) % n if random.random() < 0.5 else (p2 - 1) % n
            
            # Wait, if p1_next == p2_next, the game ends.
            # This happens if p1 and p2 were at distance 2 and they both move to the same person.
            # Or if they were at distance 0 (but they start at distance 1).
            
            # Let's re-read: "the people who has a gift is giving it to each of his neighbor by 1/2 probability"
            # This is still the most confusing part.
            # If it means "for each neighbor, the probability is 1/2", then:
            # p1_targets = []
            # if random.random() < 0.5: p1_targets.append((p1-1)%n)
            # if random.random() < 0.5: p1_targets.append((p1+1)%n)
            # This would mean the number of gifts can change.
            # But "the game ends if a people got the two gifts (from his neighbors)".
            # This implies that the gifts are being passed.
            # If a person has a gift, he gives it to his neighbors.
            # This sounds like the gifts are moving.
            
            # Let's try the interpretation:
            # Each gift moves to one of its two neighbors with probability 1/2.
            # p1_next = (p1 + 1) % n if random.random() < 0.5 else (p1 - 1) % n
            # p2_next = (p2 + 1) % n if random.random() < 0.5 else (p2 - 1) % n
            # If p1_next == p2_next, the game ends.
            
            p1_next = (p1 + 1) % n if random.random() < 0.5 else (p1 - 1) % n
            p2_next = (p2 + 1) % n if random.random() < 0.5 else (p2 - 1) % n
            
            if p1_next == p2_next:
                # Game ends at time 'time'
                total_time += time
                break
            
            p1, p2 = p1_next, p2_next
            
            # Check special condition: "between the two gifts there are exactly 10 and n-12 friends"
            # This means the two gaps are 10 and n-12.
            # This is equivalent to saying the distance is 11.
            # (p1 - p2) % n == 11 or (p2 - p1) % n == 11
            d = (p1 - p2) % n
            if d == 11 or d == n - 11:
                special_count += 1
        
        total_count_special += special_count
        
    return total_time / trials, total_count_special / trials

# Let's test for small n
# For n=3, p1=0, p2=1.
# p1_next can be 1 or 2. p2_next can be 2 or 0.
# Possible (p1_next, p2_next): (1, 2), (1, 0), (2, 2), (2, 0).
# If (2, 2), game ends at t=1.
# If (1, 0), p1_next=1, p2_next=0. Distance is 1.
# If (1, 2), p1_next=1, p2_next=2. Distance is 1.
# If (2, 0), p1_next=2, p2_next=0. Distance is 2.
# Wait, if n=3, distance 1 and 2 are the same.
# Let's run for n=3.
print(f"n=3: {simulate(3, 10000)}")
print(f"n=4: {simulate(4, 10000)}")
print(f"n=5: {simulate(5, 10000)}")
