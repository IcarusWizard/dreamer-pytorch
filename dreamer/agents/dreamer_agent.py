import torch
from rlpyt.agents.base import BaseAgent, RecurrentAgentMixin, AgentStep
from rlpyt.utils.buffer import buffer_to, buffer_func
from rlpyt.utils.collections import namedarraytuple

from dreamer.models.agent import AgentModel

DreamerAgentInfo = namedarraytuple('DreamerAgentInfo', ['prev_state'])


# see classes BaseAgent and RecurrentAgentMixin for documentation
class DreamerAgent(RecurrentAgentMixin, BaseAgent):

    def __init__(self, ModelCls=AgentModel, model_kwargs=None, initial_model_state_dict=None):
        super().__init__(ModelCls, model_kwargs, initial_model_state_dict)

    def make_env_to_model_kwargs(self, env_spaces):
        """Generate any keyword args to the model which depend on environment interfaces."""
        return dict(action_size=env_spaces.action.shape[0])

    def __call__(self, observation, prev_action, init_rnn_state):
        model_inputs = buffer_to((observation, prev_action, init_rnn_state), device=self.device)
        return self.model(*model_inputs)

    @torch.no_grad()
    def step(self, observation, prev_action, prev_reward):
        """"
        Compute policy's action distribution from inputs, and sample an
        action. Calls the model to produce mean, log_std, value estimate, and
        next recurrent state.  Moves inputs to device and returns outputs back
        to CPU, for the sampler.  Advances the recurrent state of the agent.
        (no grad)
        """
        model_inputs = buffer_to((observation, prev_action), device=self.device)
        action, state = self.model(*model_inputs, self.prev_rnn_state)
        # Model handles None, but Buffer does not, make zeros if needed:
        prev_state = self.prev_rnn_state or buffer_func(state, torch.zeros_like)
        self.advance_rnn_state(state)
        agent_info = DreamerAgentInfo(prev_state=prev_state)
        agent_step = AgentStep(action=action, agent_info=agent_info)
        return buffer_to(agent_step, device='cpu')

    @torch.no_grad()
    def value(self, observation, prev_action, prev_reward):
        """
        Compute the value estimate for the environment state using the
        currently held recurrent state, without advancing the recurrent state,
        e.g. for the bootstrap value V(s_{T+1}), in the sampler.  (no grad)
        """
        agent_inputs = buffer_to((observation, prev_action), device=self.device)
        action, action_dist, value, reward, state = self.model(*agent_inputs, self.prev_rnn_state)
        return value.to("cpu")
