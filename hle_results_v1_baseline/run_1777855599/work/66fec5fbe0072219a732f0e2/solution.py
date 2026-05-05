import numpy as np

def solve():
    # The problem asks for the expectation of the time until the second collision.
    # There are 5 particles.
    # Let positions be x1 < x2 < x3 < x4 < x5.
    # Collisions happen between adjacent particles.
    # When two particles collide, they annihilate.
    # Initially, rates are 1. After first collision, rates are 2.
    # This is a continuous-time Markov process.
    # However, the state space is infinite.
    # But the problem might be solvable using properties of random walks.
    
    # Let's reconsider the problem.
    # 5 particles: P1, P2, P3, P4, P5.
    # Initial positions: 0, N1, N1+M1, N1+M1+N2, N1+M1+N2+M2.
    # Let's denote them as x1, x2, x3, x4, x5.
    # x1 = 0
    # x2 = N1
    # x3 = N1 + M1
    # x4 = N1 + M1 + N2
    # x5 = N1 + M1 + N2 + M2
    
    # The distance between adjacent particles:
    # d1 = x2 - x1 = N1
    # d2 = x3 - x2 = M1
    # d3 = x4 - x3 = N2
    # d4 = x5 - x4 = M2
    
    # A collision occurs when di = 0.
    # For a simple random walk with rate lambda, the difference of two adjacent particles
    # is a random walk with rate 2*lambda (since each moves with rate lambda, 
    # and the difference moves with rate lambda + lambda = 2*lambda).
    # Wait, the difference of two independent random walks with rate lambda:
    # X_t - Y_t.
    # X_t jumps +1 with prob 1/2, -1 with prob 1/2, rate lambda.
    # Y_t jumps +1 with prob 1/2, -1 with prob 1/2, rate lambda.
    # X_t - Y_t jumps:
    # +2 with prob (lambda/2)*(1/2) = lambda/4? No.
    # The jumps of X_t and Y_t are independent.
    # X_t jumps at rate lambda. Y_t jumps at rate lambda.
    # Total jump rate is 2*lambda.
    # When X_t jumps:
    #   X_t -> X_t + 1 (prob 1/2): (X-Y) -> (X-Y) + 1
    #   X_t -> X_t - 1 (prob 1/2): (X-Y) -> (X-Y) - 1
    # When Y_t jumps:
    #   Y_t -> Y_t + 1 (prob 1/2): (X-Y) -> (X-Y) - 1
    #   Y_t -> Y_t - 1 (prob 1/2): (X-Y) -> (X-Y) + 1
    # So (X-Y) jumps +1 with rate lambda/2 + lambda/2 = lambda.
    # And (X-Y) jumps -1 with rate lambda/2 + lambda/2 = lambda.
    # Total rate of (X-Y) jumping is 2*lambda.
    # This is a simple random walk with rate 2*lambda.
    
    # Wait, the problem says "the remaining particles start moving with rate 2".
    # This means the rate of each particle becomes 2.
    # So the rate of the difference becomes 2 * 2 = 4.
    
    # Let's simplify.
    # We have 5 particles. Let's call the gaps g1, g2, g3, g4.
    # g1 = x2 - x1, g2 = x3 - x2, g3 = x4 - x3, g4 = x5 - x4.
    # A collision occurs when some gi = 0.
    # When gi = 0, particles i and i+1 annihilate.
    # This reduces the number of particles by 2 and changes the gaps.
    # If i and i+1 annihilate:
    # The new gaps will be:
    # If i=1: g2 becomes g2+g1? No.
    # If x1, x2, x3, x4, x5 are positions.
    # If x1, x2 collide: x1=x2. They are removed.
    # Remaining: x3, x4, x5.
    # New gaps: g2' = x3 - x4 (wait, x3-x4 is -g3), no.
    # The new gaps are the distances between the remaining particles.
    # If x1, x2 are removed, the remaining are x3, x4, x5.
    # The gaps are x4-x3 and x5-x4.
    # If x2, x3 are removed, the remaining are x1, x4, x5.
    # The gaps are x4-x1 and x5-x4.
    
    # This looks like the problem of "annihilating random walks".
    # For 2 particles at distance d, the time to collision is the time for a random walk
    # with rate 2*lambda to hit 0.
    # But for a simple random walk on Z, the expected time to hit 0 is infinite.
    # Wait, the problem is on Z. The expected time for a random walk to hit 0 is infinite.
    # Let me re-read. "Find the expectation of tau".
    # If the expectation is finite, there must be something I'm missing.
    # Is it possible the particles are on a finite graph? No, "on Z".
    # Is it possible the rates or the setup make it finite?
    # Let's check the "rate lambda" part again.
    # "an independent Poisson process of rate lambda is associated to a particle, 
    # and the events of that process correspond to times when the particle jumps 
    # to the left or to the right with equal probabilities."
    # This is exactly what I described.
    # For a single particle, the position is X_t.
    # For two particles, X_t and Y_t, the distance is D_t = |X_t - Y_t|.
    # D_t is a random walk on {0, 1, 2, ...} with jumps +1 and -1.
    # But it's not a simple random walk because it's reflected at 0.
    # Actually, we want the time to hit 0.
    # For a symmetric random walk on Z, the expected time to hit 0 starting from d > 0 is infinite.
    # Let me double check this. Yes, for a symmetric random walk, the hitting time is almost surely finite, 
    # but its expectation is infinite.
    
    # Wait, if the expectation is infinite, the answer would be "infinity".
    # But usually these problems have a finite answer.
    # Let me re-read carefully.
    # "There are initially five particles... Find the expectation of tau."
    # Is there any other detail?
    # "When some two particles collide, they annihilate... At first all particles move with rate 1; 
    # when the first collision happens, the remaining particles start moving with rate 2."
    # "Let tau be the time when the second collision happens, i.e., only one particle remains in the system."
    # If there are 5 particles, the first collision reduces it to 3.
    # The second collision reduces it to 1.
    # So tau is the time of the second collision.
    
    # Is it possible the particles are not on Z but on a circle? No, "on Z".
    # Is it possible the random walk is not symmetric? "left or to the right with equal probabilities".
    # Is it possible the rates are different? "rate 1", then "rate 2".
    
    # Let's re-examine the "expected time to hit 0" for a random walk.
    # If the walk is biased, the expectation is finite. But this is symmetric.
    # If the walk is on a finite interval [0, L], the expectation is finite.
    # But this is on Z.
    
    # Wait! "When some two particles collide, they annihilate".
    # This is a known problem. For $n$ particles on a line, the annihilation process.
    # But the expectation of the time is usually infinite for symmetric walks.
    
    # Let me search for this specific problem online.
    # Maybe it's from a specific competition (like Putnam, IMO Shortlist, etc.)?
    # The notation $N_1, M_1, N_2, M_2$ is very specific.
    
    pass

solve()
