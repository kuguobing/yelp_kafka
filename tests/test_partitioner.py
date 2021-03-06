# -*- coding: utf-8 -*-
# Copyright 2016 Yelp Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from __future__ import absolute_import
from __future__ import unicode_literals

import hashlib

import mock
import pytest
from kafka.util import kafka_bytestring
from kazoo.protocol.states import KazooState
from kazoo.recipe.partitioner import PartitionState
from kazoo.recipe.partitioner import SetPartitioner

from yelp_kafka.config import KafkaConsumerConfig
from yelp_kafka.error import PartitionerError
from yelp_kafka.error import PartitionerZookeeperError
from yelp_kafka.partitioner import Partitioner


def get_partitioner_state(status):
    return {'state': status}


class TestPartitioner(object):

    topics = ['topic1', 'topic2']

    sha = hashlib.sha1(repr(sorted(topics)).encode()).hexdigest()

    @pytest.fixture
    @mock.patch('yelp_kafka.partitioner.KazooClient', autospec=True)
    @mock.patch('yelp_kafka.partitioner.KafkaClient', autospec=True)
    def partitioner(self, kazoo, kafka, config):
        return Partitioner(config, self.topics, mock.Mock(), mock.Mock())

    def test_partitioner_use_sha(self, cluster):
        config = KafkaConsumerConfig(
            cluster=cluster,
            group_id='test_group',
            client_id='test_client_id',
            partitioner_cooldown=0.5,
            use_group_sha=True,
            pre_rebalance_callback=mock.Mock(),
            post_rebalance_callback=mock.Mock(),
        )
        p = Partitioner(config, self.topics, mock.Mock(), mock.Mock())

        assert p.zk_group_path == '/yelp-kafka/test_group/{sha}'.format(sha=self.sha)

    def test_partitioner_use_sha_false(self, cluster):
        config = KafkaConsumerConfig(
            cluster=cluster,
            group_id='test_group',
            client_id='test_client_id',
            partitioner_cooldown=0.5,
            use_group_sha=False,
            pre_rebalance_callback=mock.Mock(),
            post_rebalance_callback=mock.Mock(),
        )
        p = Partitioner(config, self.topics, mock.Mock(), mock.Mock())

        assert p.zk_group_path == '/yelp-kafka/test_group'

    def test_get_partitions_set(self, partitioner):
        with mock.patch(
            'yelp_kafka.partitioner.get_kafka_topics',
            autospec=True
        ) as mock_topics:
            mock_topics.return_value = {
                kafka_bytestring('topic1'): [0, 1, 2, 3],
                kafka_bytestring('topic2'): [0, 1, 2],
                kafka_bytestring('topic3'): [0, 1, 2, 3],
            }
            actual = partitioner.get_partitions_set()
            assert actual == set([
                'topic1-0', 'topic1-1', 'topic1-2', 'topic1-3',
                'topic2-0', 'topic2-1', 'topic2-2'
            ])

    def test_handle_release(self, partitioner):
        mock_kpartitioner = mock.MagicMock(
            spec=SetPartitioner, **get_partitioner_state(PartitionState.RELEASE)
        )
        expected_partitions = {'topic1': [0, 1, 3]}
        # Enable release
        partitioner.acquired_partitions = expected_partitions
        partitioner.released_flag = False

        partitioner._handle_group(mock_kpartitioner)

        mock_kpartitioner.release_set.assert_called_once_with()
        partitioner.release.assert_called_once_with(expected_partitions)

    def test_handle_release_twice(self, partitioner):
        mock_kpartitioner = mock.MagicMock(
            spec=SetPartitioner, **get_partitioner_state(PartitionState.RELEASE)
        )
        expected_partitions = {'topic1': [0, 1, 3]}
        # Enable release
        partitioner.acquired_partitions = expected_partitions
        partitioner.released_flag = False

        partitioner._handle_group(mock_kpartitioner)
        partitioner._handle_group(mock_kpartitioner)

        mock_kpartitioner.release_set.call_count == 2
        # User release function should be called only once
        partitioner.release.assert_called_once_with(expected_partitions)

    def test_handle_release_failure(self, partitioner):
        mock_kpartitioner = mock.MagicMock(
            spec=SetPartitioner, **get_partitioner_state(PartitionState.RELEASE)
        )
        expected_partitions = {'topic1': [0, 1, 3]}
        # Enable release
        partitioner.acquired_partitions = expected_partitions
        partitioner.released_flag = False
        partitioner.release.side_effect = Exception("Boom!")

        with pytest.raises(PartitionerError):
            partitioner._handle_group(mock_kpartitioner)

    def test_handle_failed_and_release(self, partitioner):
        mock_kpartitioner = mock.MagicMock(
            spec=SetPartitioner,
            **get_partitioner_state(PartitionState.FAILURE)
        )
        expected_partitions = {'topic1': [0, 1, 3]}
        partitioner.acquired_partitions = expected_partitions
        with mock.patch.object(Partitioner, 'release_and_finish') as mock_destroy:
            with pytest.raises(PartitionerZookeeperError):
                partitioner._handle_group(mock_kpartitioner)
            assert mock_destroy.call_count == 1

    def test_handle_failed_and_release_no_acquired_partitions(self, partitioner):
        mock_kpartitioner = mock.MagicMock(
            spec=SetPartitioner,
            **get_partitioner_state(PartitionState.FAILURE)
        )
        with mock.patch.object(Partitioner, 'release_and_finish') as mock_destroy:
            with pytest.raises(PartitionerZookeeperError):
                partitioner._handle_group(mock_kpartitioner)
            assert mock_destroy.call_count == 1

    def test_handle_acquired(self, partitioner):
        mock_kpartitioner = mock.MagicMock(
            spec=SetPartitioner, **get_partitioner_state(PartitionState.ACQUIRED)
        )
        mock_kpartitioner.__iter__.return_value = ['topic1-0', 'topic1-2', 'topic-2-1']
        expected_partitions = {'topic1': [0, 2], 'topic-2': [1]}

        partitioner._handle_group(mock_kpartitioner)

        assert partitioner.acquired_partitions == expected_partitions
        assert partitioner.released_flag is False
        partitioner.acquire.assert_called_once_with(expected_partitions)

    def test_handle_acquire_failure(self, partitioner):
        mock_kpartitioner = mock.MagicMock(
            spec=SetPartitioner, **get_partitioner_state(PartitionState.ACQUIRED)
        )
        mock_kpartitioner.__iter__.return_value = ['topic1-0', 'topic1-2', 'topic-2-1']
        partitioner.acquire.side_effect = Exception("Boom!")

        with pytest.raises(PartitionerError):
            partitioner._handle_group(mock_kpartitioner)

    def test_handle_allocating(self, partitioner):
        mock_kpartitioner = mock.MagicMock(
            spec=SetPartitioner, **get_partitioner_state(PartitionState.ALLOCATING)
        )
        partitioner._handle_group(mock_kpartitioner)
        mock_kpartitioner.wait_for_acquire.assert_called_once_with()

    def test__get_partitioner_no_partitions_change(self, partitioner):
        expected_partitions = set(['top-1', 'top1-2'])
        with mock.patch.object(
            Partitioner,
            '_create_partitioner',
            side_effect=[mock.sentinel.partitioner1, mock.sentinel.partitioner2]
        ) as mock_create:
            with mock.patch.object(Partitioner, 'get_partitions_set') as mock_partitions:
                mock_partitions.return_value = expected_partitions
                actual = partitioner._get_partitioner()

                assert actual == mock.sentinel.partitioner1
                assert partitioner.partitions_set == expected_partitions
                assert not partitioner.need_partitions_refresh()

                # Call the partitioner again with the same partitions set and be sure
                # it does not create a new one
                partitioner.force_partitions_refresh = True

                actual = partitioner._get_partitioner()

                assert partitioner.partitions_set is expected_partitions
                assert actual == mock.sentinel.partitioner1
                assert mock_create.call_count == 1
                assert not partitioner.need_partitions_refresh()

    def test__get_partitioner_partitions_change(self, partitioner):
        # We create a new partitioner, then we change the partitions
        # and we expect the partitioner to be destroyed.
        expected_partitions = set(['top-1', 'top1-2'])

        create_side_effect = [mock.sentinel.partitioner1, mock.sentinel.partitioner2]
        with mock.patch.object(Partitioner, '_create_partitioner', side_effect=create_side_effect) as mock_create:
            with mock.patch.object(Partitioner, 'release_and_finish') as mock_destroy:
                with mock.patch.object(Partitioner, 'get_partitions_set') as mock_partitions:
                    mock_partitions.return_value = expected_partitions
                    # force partitions refresh is True when the partitioner starts
                    assert partitioner.need_partitions_refresh()
                    actual = partitioner._get_partitioner()
                    assert actual == mock.sentinel.partitioner1
                    assert partitioner.partitions_set == expected_partitions
                    assert not partitioner.need_partitions_refresh()

                    # Change the partitions and test the partitioner gets destroyed for
                    # rebalancing
                    partitioner.force_partitions_refresh = True
                    new_expected_partitions = set(['top-1', 'top1-2', 'top1-3'])
                    mock_partitions.return_value = new_expected_partitions
                    actual = partitioner._get_partitioner()
                    assert partitioner.partitions_set is new_expected_partitions
                    assert mock_destroy.called
                    assert actual == mock.sentinel.partitioner2
                    assert mock_create.call_count == 2
                    assert not partitioner.need_partitions_refresh()

    @mock.patch('yelp_kafka.partitioner.KafkaClient')
    @mock.patch('yelp_kafka.partitioner.KazooClient')
    def test__close_connections(self, mock_kazoo, mock_kafka, config):
        partitioner = Partitioner(config, self.topics, mock.Mock(), mock.Mock())
        with mock.patch.object(
            Partitioner, '_refresh'
        ) as mock_refresh:
            # start the partitioner and verify that we refresh the partition set
            partitioner.start()
            mock_refresh.assert_called_once_with()
            # destroy the partitioner and ensure we cleanup all open handles.
            partitioner._close_connections()
            # did we close all open connections with kafka and zk?
            mock_kazoo.return_value.stop.assert_called_once_with()
            mock_kazoo.return_value.close.assert_called_once_with()
            mock_kafka.return_value.close.assert_called_once_with()
            assert partitioner.partitions_set == set()
            assert partitioner._partitioner is None
            assert partitioner.last_partitions_refresh == 0

    @mock.patch('yelp_kafka.partitioner.KafkaClient', autospec=True)
    @mock.patch('yelp_kafka.partitioner.KazooClient')
    def test__create_partitioner_with_kazoo_connection(
        self,
        mock_kazoo,
        _,
        config,
    ):
        # Mock a successful connection to zookeeper
        mock_kpartitioner = mock.MagicMock(spec=SetPartitioner)
        mock_kazoo.return_value.SetPartitioner.return_value = mock_kpartitioner
        mock_kazoo.return_value.state = KazooState.CONNECTED
        partitioner = Partitioner(
            config,
            self.topics,
            mock.Mock(),
            mock.Mock(),
        )
        # Verify that we distribute the partitions
        # when we start the partitioner
        with mock.patch.object(Partitioner, '_refresh') as mock_refresh:
            partitioner.start()
            mock_refresh.assert_called_once_with()
            expected_partitions = set(['topic1-1', 'topic1-2'])
            assert mock_kpartitioner == partitioner._create_partitioner(
                expected_partitions
            )
            mock_kazoo.return_value.SetPartitioner.assert_called_once_with(
                path='/yelp-kafka/test_group/{sha}'.format(sha=self.sha),
                set=expected_partitions,
                time_boundary=0.5
            )
            assert not mock_kazoo.return_value.start.called

    @mock.patch('yelp_kafka.partitioner.KafkaClient', autospec=True)
    @mock.patch('yelp_kafka.partitioner.KazooClient')
    def test__create_partitioner_no_kazoo_connection(
        self,
        mock_kazoo,
        _,
        config,
    ):
        # Mock a failed connection to Zookeeper
        mock_kpartitioner = mock.MagicMock(spec=SetPartitioner)
        mock_kazoo.return_value.SetPartitioner.return_value = mock_kpartitioner
        mock_kazoo.return_value.state = KazooState.LOST
        partitioner = Partitioner(
            config,
            self.topics,
            mock.Mock(),
            mock.Mock(),
        )
        # Verify that we attempt to re-establish the connection with Zookeeper
        # and distribute the partitions.
        with mock.patch.object(Partitioner, '_refresh') as mock_refresh:
            partitioner.start()
            mock_refresh.assert_called_once_with()
            expected_partitions = set(['topic1-1', 'topic1-2'])
            assert mock_kpartitioner == partitioner._create_partitioner(
                expected_partitions
            )
            mock_kazoo.return_value.SetPartitioner.assert_called_once_with(
                path='/yelp-kafka/test_group/{sha}'.format(sha=self.sha),
                set=expected_partitions,
                time_boundary=0.5
            )
            assert mock_kazoo.return_value.start.call_count == 1

    def test_get_partitions_kafka_unavailable(self, partitioner):
        expected_partitions = set(['fake-topic'])
        with mock.patch.object(Partitioner, '_create_partitioner') as mock_create:
            with mock.patch.object(Partitioner, 'get_partitions_set') as mock_partitions:
                mock_create.return_value = mock.sentinel.partitioner
                mock_partitions.return_value = expected_partitions
                # Initialize partitioner
                actual = partitioner._get_partitioner()

                assert actual == mock.sentinel.partitioner
                assert mock_create.call_count == 1

        with mock.patch.object(Partitioner, 'get_partitions_set', side_effect=Exception("Boom!")) as mock_partitions:
            with mock.patch.object(Partitioner, 'release_and_finish') as mock_destroy:
                # Force partition refresh
                partitioner.force_partitions_refresh = True

                with pytest.raises(PartitionerError):
                    partitioner._get_partitioner()

                assert mock_destroy.called

    def test_release_and_finish(self, partitioner):
        with mock.patch.object(
            Partitioner,
            '_release',
        ) as mock_release:
            # Attach a mocked partitioner and kafka client
            mock_kpartitioner = mock.MagicMock(spec=SetPartitioner)
            partitioner._partitioner = mock_kpartitioner

            partitioner.release_and_finish()

            mock_kpartitioner.finish.assert_called_once_with()
            assert partitioner._partitioner is None
            mock_release.assert_called_once_with(mock_kpartitioner)
