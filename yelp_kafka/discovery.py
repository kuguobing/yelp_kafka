import logging
import os
import re

from kafka import KafkaClient

from yelp_kafka.config import YelpKafkaConfig
from yelp_kafka.config import TopologyConfiguration
from yelp_kafka.error import DiscoveryError
from yelp_kafka.utils import get_kafka_topics
from yelp_kafka.utils import make_scribe_topic


REGION_PATH = '/nail/etc/region'
DEFAULT_KAFKA_SCRIBE = 'scribe'


log = logging.getLogger(__name__)


def get_local_region():
    """Get the region where this code is running.

    :raises :py:class:`yelp_kafka.error.DiscoveryError`:
        if /nail/etc/region is not in the filesystem.
    """
    if os.path.exists(REGION_PATH):
        with open(REGION_PATH) as fd:
            return fd.read().strip()
    else:
        raise DiscoveryError("Something is seriously wrong."
                             "Can't find a region file in this box")


def get_clusters_config(cluster_type, region=None):
    """Get a list of tuples (cluster_name, :py:class:`yelp_kafka.config.ClusterConfig`)
    for the kafka clusters in region.

    :param cluster_type: the id of the kafka cluster
        (ex.'scribe' or 'standard').
    :type cluster_type: string
    :param region: the name of the region where to look for the cluster.
        Default: local region.
    :type region: string
    :returns: list (cluster_name, :py:class:`yelp_kafka.config.ClusterConfig`)
    """
    topology = TopologyConfiguration(kafka_id=cluster_type)
    if not region:
        region = get_local_region()
    return topology.get_clusters_for_region(region=region)


def get_yelp_kafka_config(cluster_type, group_id, region=None, **extra):
    """Get a list of tuples (cluster_name, :py:class:`yelp_kafka.config.YelpKafkaConfig`)
    for the kafka clusters in region.

    :param cluster_type: the id of the kafka cluster
        (ex.'scribe' or 'standard').
    :type cluster_type: string
    :param group_id: consumer group id
    :type group_id: string
    :param region: the name of the region where to look for the cluster.
        Default: local region.
    :type region: string
    :param extra: extra arguments to use for creating the configuration
    :returns: list (cluster_name, :py:class:`yelp_kafka.config.ClusterConfig`)
    """
    clusters = get_clusters_config(cluster_type, region=region)
    return [(name, YelpKafkaConfig(group_id=group_id, cluster=cluster, **extra))
            for name, cluster in clusters]


def get_clusters_broker_list(cluster_type, region=None):
    """Get a list (cluster_name, [broker_list]) for the kafka
    clusters in region.

    :param cluster_type: the id of the kafka cluster
        (ex.'scribe' or 'standard').
    :type cluster_type: string
    :param region: the name of the region where to look for the cluster.
        Default: local region.
    :type region: string
    :returns: list (cluster_name, [broker_list])
    """
    clusters = get_clusters_config(cluster_type, region)
    return [(name, config.broker_list) for name, config in clusters]


def get_all_clusters_config(cluster_type):
    """Get all the clusters for all the region.

    :param cluster_type: the id of the kafka cluster
        (ex.'scribe' or 'standard').
    :type cluster_type: string
    :returns: list
    (region, [(cluster_name, :py:class:`yelp_kafka.config.ClusterConfig`)])

    .. note: the name cluster will likely be in many regions.
    """
    topology = TopologyConfiguration(kafka_id=cluster_type)
    return topology.get_all_clusters()


def get_kafka_connection(cluster_type, client_id='yelp-kafka', region=None):
    """Get a kafka connection for each cluster in region.

    :param cluster_type: the id of the kafka cluster
        (ex.'scribe' or 'standard').
    :type cluster_type: string
    :param client_id: client_id to be used to connect to kafka.
    :type client_id: string
    :param region: the name of the region where to look for the cluster.
        Default: local region.
    :type region: string
    :returns: list (cluster, KafkaClient)
    :raises :py:class:`yelp_kafka.error.DiscoveryError`: upon failure connecting
        connecting to a cluster.

    .. note: This function create a KafkaClient for each cluster in a region
       and tries to connect to it. If a cluster is not available it fails and
       closes all the previous connections.
    .. warning: Connecting to a cluster fails if the cluster is not in
       the current runtime env.
    """

    clusters = get_clusters_config(cluster_type, region)
    connected_clusters = []
    for cluster_name, cluster_config in clusters:
        try:
            client = KafkaClient(cluster_config.broker_list, client_id=client_id)
            connected_clusters.append((cluster_name, client))
        except:
            log.exception("Connection to kafka cluster %s using broker"
                          " list %s failed", cluster_name,
                          cluster_config.broker_list)
            for cluster, client in connected_clusters:
                client.close()
            raise DiscoveryError("Failed to connect to cluster {0}".format(
                cluster_name))
    return connected_clusters


def discover_topics(cluster_config):
    """Get all the topics in a cluster
    :param cluster_config: config of the cluster to get topics from
    :type cluster_config: :py:class:`yelp_kafka.config.ClusterConfig`
    :returns: a dict <topic>: <[partitions]>
    """
    try:
        return get_kafka_topics(cluster_config.broker_list)
    except:
        log.exception("Topics discovery failed for %s",
                      cluster_config.broker_list)
        raise DiscoveryError("Failed to get topics information from "
                             "{0}".format(cluster_config.broker_list))


def search_topic(cluster_type, topic, region=None):
    """Search for a topic in a specific cluster.

    :param cluster_type: the id of the kafka cluster
        (ex.'scribe' or 'standard').
    :type cluster_type: string
    :param topic: topic name
    :type topic: string
    :returns: (topic, cluster_name, cluster_config).

    .. note: you can only search for topics in the current runtime env.
    """
    matches = []
    clusters = get_clusters_config(cluster_type, region)
    for name, config in clusters:
        topics = discover_topics(config)
        if topic in topics.keys():
            matches.append((topic, name, config))
    return matches


def search_topic_by_regex(cluster_type, pattern, region=None):
    """Search for all the topics matching pattern.

    :param cluster_type: the id of the kafka cluster
        (ex.'scribe' or 'standard').
    :type cluster_type: string
    :param pattern: regex to match topics
    :param region: the name of the region where to look for the cluster.
        Default: local region.
    :type region: string
    :returns: a list of tuples ([topics], cluster_name, cluster_config).

    .. note: you can only search for topics in the current runtime env.
    """
    matches = []
    clusters = get_clusters_config(cluster_type, region)
    for name, config in clusters:
        topics = discover_topics(config)
        valid_topics = [topic for topic in topics.iterkeys()
                        if re.match(pattern, topic)]
        if valid_topics:
            matches.append((valid_topics, name, config))
    return matches


def get_scribe_topic_in_datacenter(
    stream, datacenter,
    cluster_type=DEFAULT_KAFKA_SCRIBE, region=None
):
    """Search for the scribe topic for a scribe stream in the specified
    datacenter.

    :param stream: scribe stream name
    :type stream: string
    :param datacenter: datacenter name
    :type datacenter: string
    :param cluster_type: the id of the kafka cluster. Default: scribe.
    :type cluster_type: string
    :param region: the name of the region where to look for the cluster.
        Default: local region.
    :type region: string
    :returns: [(topic, cluster_name, cluster_config)]

    .. note: you can only search for topics in the current runtime env.
    """
    return search_topic(cluster_type,
                        make_scribe_topic(stream, datacenter),
                        region)


def get_scribe_topics(
    stream, cluster_type=DEFAULT_KAFKA_SCRIBE, region=None
):
    """Search for all the topics for a scribe stream.

    :param stream: scribe stream name
    :type stream: string
    :param cluster_type: the id of the kafka cluster. Default: scribe.
    :type cluster_type: string
    :param region: the name of the region where to look for the cluster.
        Default: local region.
    :returns: [([topics], cluster_name, cluster_config)]

    .. note: you can only search for topics in the current runtime env.
    """
    pattern = '^scribe\.\w*\.{0}$'.format(stream)
    return search_topic_by_regex(cluster_type,
                                 pattern,
                                 region)
