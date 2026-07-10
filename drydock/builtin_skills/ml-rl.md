---
name: ml-rl
description: Implement and train a reinforcement-learning agent to solve an environment
---
Reinforcement-learning task: $ARGS

1. Inspect the environment API — `reset()`, `step(action)` return values, the state
   and action spaces. Look at the actual code before coding the agent.
2. Choose the method:
   - Small discrete states → tabular Q-learning: `Q=np.zeros((nS,nA))`; epsilon-greedy
     action; Bellman update `Q[s,a]+=alpha*(r+gamma*Q[s2].max()-Q[s,a])`; decay epsilon.
   - Large/continuous → a neural policy: DQN (replay buffer + target network) or
     REINFORCE (maximize sum of log_prob(a)*return).
3. Train until the average episode reward rises and the greedy policy solves the task.
   alpha≈0.5, gamma≈0.95, eps 0.1–0.2 decaying. Save the policy/Q-table exactly as
   asked and verify the greedy policy reaches the goal.
