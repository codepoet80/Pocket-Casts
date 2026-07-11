"""Unofficial API for pocketcasts.com"""
import requests
from .podcast import Podcast
from .episode import Episode

__version__ = "0.2.3"
__author__ = "Fergus Longley"
__url__ = "https://github.com/exofudge/Pocket-Casts"


class Pocketcasts(object):
    """The main class for making getting and setting information from the server"""

    # Modern Pocket Casts backends (the legacy play.pocketcasts.com/web/* API was retired).
    API = "https://api.pocketcasts.com"
    CACHE = "https://cache.pocketcasts.com"

    @staticmethod
    def _norm_podcast(raw):
        """Normalise a podcast dict from any modern endpoint into the kwargs the
        Podcast class expects. Returns (uuid, kwargs)."""
        raw = dict(raw)
        uuid = raw.pop('uuid', '')
        kwargs = {
            'id': raw.get('id', ''),
            'title': raw.get('title', ''),
            'author': raw.get('author', ''),
            'description': raw.get('description', ''),
            'url': raw.get('url', ''),
            'language': raw.get('language', ''),
            'category': raw.get('category', ''),
            'media_type': raw.get('mediaType', raw.get('media_type', '')),
            'thumbnail_url': raw.get('thumbnail_url', ''),
            # camelCase (user/podcast/list) or snake_case (legacy) sort order
            'episodes_sort_order': raw.get('episodesSortOrder',
                                           raw.get('episodes_sort_order', 3)),
        }
        return uuid, kwargs

    @staticmethod
    def _norm_episode(raw):
        """Normalise an episode dict from any modern endpoint into the kwargs the
        Episode class expects. Handles both the cache host (snake_case:
        file_type/file_size) and the user endpoints (camelCase:
        fileType/size/playingStatus/playedUpTo). Returns (uuid, kwargs)."""
        raw = dict(raw)
        uuid = raw.pop('uuid', '')
        kwargs = {
            'id': raw.get('id', ''),
            'is_deleted': raw.get('isDeleted', raw.get('is_deleted', '')),
            'is_video': raw.get('isVideo', raw.get('is_video', '')),
            'file_type': raw.get('fileType', raw.get('file_type', '')),
            'size': raw.get('size', raw.get('file_size', '')),
            'title': raw.get('title', ''),
            'url': raw.get('url', ''),
            'duration': raw.get('duration', ''),
            'published_at': raw.get('published', raw.get('published_at', '')),
            'starred': raw.get('starred', ''),
            'playing_status': raw.get('playingStatus',
                                      raw.get('playing_status', Episode.PlayingStatus.Unplayed)),
            'played_up_to': raw.get('playedUpTo', raw.get('played_up_to', '')),
        }
        return uuid, kwargs

    def __init__(self, email, password):
        """

        Args:
            email (str): email of user
            password (str): password of user
        """
        self._username = email
        self._password = password
        self._token = None

        self._session = requests.Session()
        self._login()

    def _make_req(self, url, method='GET', data=None):
        """Makes a HTTP GET/POST request

        Args:
            url (str): The URL to make the request to
            method (str, optional): The method to use. Defaults to 'GET'
            data (dict):  data to send with a POST request. Defaults to None.

        Returns:
            requests.response.models.Response: A response object

        """
        headers = {}
        # Only the api.pocketcasts.com backend uses bearer auth. The static/cache
        # CDN hosts reject requests that carry an Authorization header.
        if self._token and url.startswith(self.API):
            headers['Authorization'] = 'Bearer {}'.format(self._token)
        if method == 'JSON':
            req = requests.Request('POST', url, json=data, cookies=self._session.cookies, headers=headers)
        elif method == 'POST' or data:
            req = requests.Request('POST', url, data=data, cookies=self._session.cookies, headers=headers)
        elif method == 'GET':
            req = requests.Request('GET', url, cookies=self._session.cookies, headers=headers)
        else:
            raise Exception("Invalid method")
        prepped = req.prepare()
        return self._session.send(prepped)

    def _login(self):
        """Authenticate against the modern Pocket Casts token API.

        The legacy "play.pocketcasts.com/users/sign_in" cookie flow was retired;
        the current backend at api.pocketcasts.com issues a bearer token instead.

        Returns:
            bool: True if successful

        Raises:
            Exception: If login fails
        """
        login_url = "https://api.pocketcasts.com/user/login"
        data = {"email": self._username, "password": self._password, "scope": "webplayer"}
        attempt = self._make_req(login_url, method='JSON', data=data)

        if attempt.status_code != 200:
            raise Exception("Login Failed: {}".format(attempt.text))
        body = attempt.json()
        # Newer responses use accessToken/token depending on API version.
        self._token = body.get('token') or body.get('accessToken')
        if not self._token:
            raise Exception("Login Failed: no token in response {}".format(body))
        return True

    @classmethod
    def from_token(cls, token):
        """Build an API client from an existing bearer token, skipping login.

        Useful for services that store a token per request/session instead of
        re-sending the user's password on every call.

        Args:
            token (str): A Pocket Casts bearer token (from ``._token`` after a login).

        Returns:
            Pocketcasts: A ready-to-use client authenticated with the given token.
        """
        self = cls.__new__(cls)
        self._username = None
        self._password = None
        self._token = token
        self._session = requests.Session()
        return self

    def get_top_charts(self):
        """Get the top podcasts

        Returns:
            list: A list of the top 100 podcasts as Podcast objects

        Raises:
            Exception: If the top charts cannot be obtained

        """
        page = self._make_req("https://static.pocketcasts.com/discover/json/popular_world.json").json()
        results = []
        for podcast in page['result']['podcasts']:
            uuid = podcast.pop('uuid')
            results.append(Podcast(uuid, self, **podcast))
        return results

    def get_featured(self):
        """Get the featured podcasts

        Returns:
            list: A list of the 30 featured podcasts as Podcast objects

        Raises:
            Exception: If the featured podcasts cannot be obtained

        """
        page = self._make_req("https://static.pocketcasts.com/discover/json/featured.json").json()
        results = []
        for podcast in page['result']['podcasts']:
            uuid = podcast.pop('uuid')
            results.append(Podcast(uuid, self, **podcast))
        return results

    def get_trending(self):
        """Get the trending podcasts

        Returns:
            list: A list of the 100 trending podcasts as Podcast objects

        Raises:
            Exception: If the trending podcasts cannot be obtained

        """
        page = self._make_req("https://static.pocketcasts.com/discover/json/trending.json").json()
        results = []
        for podcast in page['result']['podcasts']:
            uuid = podcast.pop('uuid')
            results.append(Podcast(uuid, self, **podcast))
        return results

    def get_episode(self, pod, e_uuid):
        # TODO figure out what id is/does
        """Returns an episode object corresponding to the uuid's provided

        Args:
            pod (class): The podcast class
            e_uuid (str): The episode UUID

        Returns:
            class: An Episode class with all information about an episode

        Examples:
            >>> p = Pocketcasts(email='email@email.com')
            >>> pod = p.get_podcast('12012c20-0423-012e-f9a0-00163e1b201c')
            >>> p.get_episode(pod, 'a35748e0-bb4d-0134-10a8-25324e2a541d')
            <class 'episode.Episode'> ({
            '_size': 10465287,
            '_is_video': False,
            '_url': 'http://.../2017-01-12-sysk-watersheds.mp3?awCollectionId=1003&awEpisodeId=923109',
            '_id': None,
            '_duration': '1934',
            '_is_deleted': '',
            '_title': 'How Watersheds Work',
            '_file_type': 'audio/mpeg',
            '_played_up_to': 1731,
            '_published_at': '2017-01-12 08:00:00',
            '_podcast': <class 'podcast.Podcast'> (...),
            '_playing_status': 2,
            '_starred': False,
            '_uuid': 'a35748e0-bb4d-0134-10a8-25324e2a541d'})

        """
        for episode in self.get_podcast_episodes(pod):
            if episode.uuid == e_uuid:
                return episode
        raise Exception("Episode {} not found in podcast {}".format(e_uuid, pod.uuid))

    def _get_podcast_full(self, uuid):
        """Fetch the full podcast document (metadata + all episodes) from the
        modern cache host."""
        url = "{}/podcast/full/{}/0/3/1000".format(self.CACHE, uuid)
        return self._make_req(url, method='GET').json()['podcast']

    def get_podcast(self, uuid):
        """Get a podcast from it's UUID

        Args:
            uuid (str): The UUID of the podcast

        Returns:
            pocketcasts.Podcast: A podcast object corresponding to the UUID provided.

        """
        raw = self._get_podcast_full(uuid)
        raw.pop('episodes', None)
        raw['uuid'] = uuid
        uuid, kwargs = self._norm_podcast(raw)
        return Podcast(uuid, self, **kwargs)

    def get_podcast_episodes(self, pod, sort=Podcast.SortOrder.NewestToOldest):
        """Get all episodes of a podcast

        Args:
            pod (class): The podcast class
            sort (int): The sort order, 3 for Newest to oldest, 2 for Oldest to newest. Defaults to 3.

        Returns:
            list: A list of Episode classes.

        """
        raw = self._get_podcast_full(pod.uuid)
        episodes = []
        for epi in raw.get('episodes', []):
            uuid, kwargs = self._norm_episode(epi)
            episodes.append(Episode(uuid, podcast=pod, **kwargs))
        # The cache endpoint returns episodes oldest-first; honour the sort arg.
        reverse = (sort == Podcast.SortOrder.NewestToOldest)
        episodes.sort(key=lambda e: (e.published_at is None, e.published_at), reverse=reverse)
        return episodes

    def get_episode_notes(self, episode_uuid):
        """Get the notes for an episode

        Args:
            episode_uuid (str): The episode UUID

        Returns:
            str: The notes for the episode UUID provided

        """
        url = "{}/episode/show_notes/{}".format(self.CACHE, episode_uuid)
        return self._make_req(url, method='GET').json()['show_notes']

    def _episodes_from(self, url, data=None):
        """POST to a modern user endpoint that returns {'episodes': [...]} and
        build Episode objects, resolving each episode's podcast (and caching it)."""
        attempt = self._make_req(url, method='JSON', data=data or {}).json()
        results = []
        podcasts = {}
        for episode in attempt.get('episodes', []):
            pod_uuid = episode.get('podcastUuid') or episode.get('podcast_uuid')
            if pod_uuid and pod_uuid not in podcasts:
                podcasts[pod_uuid] = self.get_podcast(pod_uuid)
            uuid, kwargs = self._norm_episode(episode)
            results.append(Episode(uuid, podcasts.get(pod_uuid), **kwargs))
        return results

    def get_subscribed_podcasts(self):
        """Get the user's subscribed podcasts

        Returns:
            List[pocketcasts.podcast.Podcast]: A list of podcasts

        """
        attempt = self._make_req("{}/user/podcast/list".format(self.API),
                                 method='JSON', data={'v': 1}).json()
        results = []
        for podcast in attempt.get('podcasts', []):
            uuid, kwargs = self._norm_podcast(podcast)
            results.append(Podcast(uuid, self, **kwargs))
        return results

    def get_new_releases(self):
        """Get newly released podcasts from a user's subscriptions

        Returns:
            List[pocketcasts.episode.Episode]: A list of episodes
        """
        return self._episodes_from("{}/user/new_releases".format(self.API))

    def get_in_progress(self):
        """Get all in progress episodes

        Returns:
            List[pocketcasts.episode.Episode]: A list of episodes

        """
        return self._episodes_from("{}/user/in_progress".format(self.API))

    def get_starred(self):
        """Get all starred episodes

        Returns:
            List[pocketcasts.episode.Episode]: A list of episodes
        """
        return self._episodes_from("{}/user/starred".format(self.API))

    def get_history(self):
        """Get the user's listening history (recently played episodes)

        Returns:
            List[pocketcasts.episode.Episode]: A list of episodes
        """
        return self._episodes_from("{}/user/history".format(self.API))

    def update_starred(self, podcast, episode, starred):
        """Star or unstar an episode

        Args:
            podcast (pocketcasts.Podcast): A podcast class
            episode (pocketcasts.Episode): An episode class to be updated
            starred (int): 1 for starred, 0 for unstarred 
        """
        data = {
            'star': bool(starred),
            'podcast': podcast.uuid,
            'uuid': episode.uuid
        }
        self._make_req("{}/sync/update_episode_star".format(self.API), method='JSON', data=data)

    def update_playing_status(self, podcast, episode, status=Episode.PlayingStatus.Unplayed):
        """Update the playing status of an episode

        Args:
            podcast (pocketcasts.Podcast): A podcast class
            episode (pocketcasts.Episode): An episode class to be updated
            status (int): 0 for unplayed, 2 for playing, 3 for played. Defaults to 0.

        """
        if status not in [0, 2, 3]:
            raise Exception('Invalid status.')
        data = {
            'status': status,
            'podcast': podcast.uuid,
            'uuid': episode.uuid
        }
        self._make_req("{}/sync/update_episode".format(self.API), method='JSON', data=data)

    def update_played_position(self, podcast, episode, position):
        """Update the current play duration of an episode

        Args:
            podcast (pocketcasts.Podcast): A podcast class
            episode (pocketcasts.Episode): An episode class to be updated
            position (int): A time in seconds

        Returns:
            bool: True if update is successful

        Raises:
            Exception: If update fails

        """
        data = {
            'uuid': episode.uuid,
            'podcast': podcast.uuid,
            'status': episode.playing_status,
            'position': position
        }
        attempt = self._make_req("{}/sync/update_episode".format(self.API), method='JSON', data=data)
        if attempt.status_code != 200:
            raise Exception('Sorry your update failed: {}'.format(attempt.text))
        return True

    def subscribe_podcast(self, podcast):
        """Subscribe to a podcast

        Args:
            podcast (pocketcasts.Podcast): The podcast to subscribe to
        """
        data = {
            'uuid': podcast.uuid
        }
        self._make_req("{}/user/podcast/subscribe".format(self.API), method='JSON', data=data)

    def unsubscribe_podcast(self, podcast):
        """Unsubscribe from a podcast

        Args:
            podcast (pocketcasts.Podcast): The podcast to unsubscribe from
        """
        data = {
            'uuid': podcast.uuid
        }
        self._make_req("{}/user/podcast/unsubscribe".format(self.API), method='JSON', data=data)

    def search_podcasts(self, search_str):
        """Search for podcasts

        Args:
            search_str (str): The string to search for

        Returns:
            List[pocketcasts.podcast.Podcast]: A list of podcasts matching the search string

        """
        data = {
            'term': search_str
        }
        attempt = self._make_req("{}/discover/search".format(self.API), method='JSON', data=data)
        results = []
        for podcast in attempt.json().get('podcasts', []):
            uuid, kwargs = self._norm_podcast(podcast)
            results.append(Podcast(uuid, self, **kwargs))
        return results
