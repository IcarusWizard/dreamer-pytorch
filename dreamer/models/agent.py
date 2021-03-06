import torch
import torch.nn as nn
from rlpyt.utils.buffer import buffer_func
from rlpyt.utils.collections import namedarraytuple
from rlpyt.utils.tensor import infer_leading_dims, restore_leading_dims, to_onehot, from_onehot

from dreamer.models.action import ActionDecoder
from dreamer.models.dense import DenseModel
from dreamer.models.observation import ObservationDecoder, ObservationEncoder
from dreamer.models.rnns import RSSMState, RSSMRepresentation, RSSMTransition, RSSMRollout, get_feat


class AgentModel(nn.Module):
    def __init__(
            self,
            output_size,
            stochastic_size=30,
            deterministic_size=200,
            hidden_size=200,
            image_shape=(3, 64, 64),
            action_hidden_size=200,
            action_layers=3,
            action_dist='tanh_normal',
            reward_shape=(1,),
            reward_layers=3,
            reward_hidden=200,
            value_shape=(1,),
            value_layers=3,
            value_hidden=200,
            dtype=torch.float,
            **kwargs,
    ):
        super().__init__()
        self.observation_encoder = ObservationEncoder(shape=image_shape)
        encoder_embed_size = self.observation_encoder.embed_size
        decoder_embed_size = stochastic_size + deterministic_size
        self.observation_decoder = ObservationDecoder(embed_size=decoder_embed_size, shape=image_shape)
        self.transition = RSSMTransition(output_size, stochastic_size, deterministic_size, hidden_size)
        self.representation = RSSMRepresentation(self.transition, encoder_embed_size, output_size, stochastic_size,
                                                 deterministic_size, hidden_size)
        self.rollout = RSSMRollout(self.representation, self.transition)
        feature_size = stochastic_size + deterministic_size
        self.action_size = output_size
        self.action_dist = action_dist
        self.action_decoder = ActionDecoder(output_size, feature_size, action_hidden_size, action_layers, action_dist)
        self.reward_model = DenseModel(feature_size, reward_shape, reward_layers, reward_hidden)
        self.value_model = DenseModel(feature_size, value_shape, value_layers, value_hidden)
        self.dtype = dtype
        self.stochastic_size = stochastic_size
        self.deterministic_size = deterministic_size

    def forward(self, observation: torch.Tensor, prev_action: torch.Tensor = None, prev_state: RSSMState = None):
        state = self.get_state_representation(observation, prev_action, prev_state)
        action, action_dist = self.policy(state)
        value = self.value_model(get_feat(state))
        reward = self.reward_model(get_feat(state))
        return action, action_dist, value, reward, state

    def policy(self, state: RSSMState):
        feat = get_feat(state)
        action_dist = self.action_decoder(feat)
        if self.action_dist == 'tanh_normal':
            if self.training:  # use agent.train(bool) or agent.eval()
                action = action_dist.sample()
            else:
                action = action_dist.mode()
        else:
            # cannot propagate gradients with one hot distribution
            action = action_dist.sample()
        return action, action_dist

    def get_state_representation(self, observation: torch.Tensor, prev_action: torch.Tensor = None,
                                 prev_state: RSSMState = None):
        """

        :param observation: size(batch, channels, width, height)
        :param prev_action: size(batch, action_size)
        :param prev_state: RSSMState: size(batch, state_size)
        :return: RSSMState
        """
        obs_embed = self.observation_encoder(observation)
        if prev_action is None:
            prev_action = torch.zeros(observation.size(0), self.action_size,
                                      device=observation.device, dtype=observation.dtype)
        if prev_state is None:
            prev_state = self.representation.initial_state(prev_action.size(0), device=prev_action.device,
                                                           dtype=prev_action.dtype)
        _, state = self.representation(obs_embed, prev_action, prev_state)
        return state

    def get_state_transition(self, prev_action: torch.Tensor, prev_state: RSSMState):
        """

        :param prev_action: size(batch, action_size)
        :param prev_state: RSSMState: size(batch, state_size)
        :return: RSSMState
        """
        state = self.transition(prev_action, prev_state)
        return state

    def forward(self, observation: torch.Tensor, prev_action: torch.Tensor = None, prev_state: RSSMState = None):
        return_spec = ModelReturnSpec(None, None)
        raise NotImplementedError()


class AtariDreamerModel(AgentModel):
    def forward(self, observation: torch.Tensor, prev_action: torch.Tensor = None, prev_state: RSSMState = None):
        lead_dim, T, B, img_shape = infer_leading_dims(observation, 3)
        observation = observation.reshape(T * B, *img_shape).type(self.dtype) / 255.0 - 0.5
        prev_action = to_onehot(prev_action.reshape(T * B, ), self.action_size, dtype=self.dtype)
        if prev_state is None:
            prev_state = self.representation.initial_state(prev_action.size(0), device=prev_action.device,
                                                           dtype=self.dtype)
        state = self.get_state_representation(observation, prev_action, prev_state)

        action, action_dist = self.policy(state)
        action = from_onehot(action)
        return_spec = ModelReturnSpec(action, state)
        return_spec = buffer_func(return_spec, restore_leading_dims, lead_dim, T, B)
        return return_spec


ModelReturnSpec = namedarraytuple('ModelReturnSpec', ['action', 'state'])
