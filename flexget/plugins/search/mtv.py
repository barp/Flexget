from __future__ import unicode_literals, division, absolute_import
from builtins import *  # pylint: disable=unused-import, redefined-builtin

import logging
import datetime
import re

from sqlalchemy import Column, Unicode, DateTime

from flexget import plugin, db_schema
from flexget.entry import Entry
from flexget.event import event
from flexget.utils.requests import TimedLimiter, RequestException
from flexget.manager import Session
from flexget.utils.database import json_synonym
from flexget.utils.requests import Session as RequestSession
from flexget.utils.soup import get_soup
from flexget.config_schema import one_or_more

log = logging.getLogger('mtv')
Base = db_schema.versioned_base('mtv', 0)

requests = RequestSession()
requests.add_domain_limiter(TimedLimiter('morethan.tv', '5 seconds'))  # TODO find out if they want a delay

CATEGORIES = {
    'Movies': 'filter_cat[1]',
    'TV': 'filter_cat[2]',
    'Other': 'filter_cat[3]'
}

TAGS = [
    'action',
    'adventure',
    'animation',
    'anime',
    'art',
    'asian',
    'biography',
    'celebrities',
    'comedy',
    'cooking',
    'crime',
    'cult',
    'documentary',
    'drama',
    'educational',
    'elclasico',
    'family',
    'fantasy',
    'film.noir',
    'filmromanesc',
    'food',
    'football',
    'formula.e',
    'formula1',
    'gameshow',
    'highlights',
    'history',
    'horror',
    'investigation',
    'lifestyle',
    'liga1',
    'ligabbva',
    'ligue1',
    'martial.arts',
    'morethan.tv',
    'motogp',
    'musical',
    'mystery',
    'nba',
    'news',
    'other',
    'performance',
    'philosophy',
    'politics',
    'reality',
    'romance',
    'romanian.content',
    'science',
    'scifi',
    'short',
    'silent',
    'sitcom',
    'sketch',
    'sports',
    'talent',
    'tennis',
    'thriller',
    'uefachampionsleague',
    'uefaeuropaleague',
    'ufc',
    'war',
    'western',
    'wta'
]


class MTVCookie(Base):
    __tablename__ = 'mtv_cookie'

    username = Column(Unicode, primary_key=True)
    _cookie = Column('cookie', Unicode)
    cookie = json_synonym('_cookie')
    expires = Column(DateTime)


class SearchMTV(object):
    """
        MorethanTV search plugin.
    """

    schema = {
        'type': 'object',
        'properties': {
            'username': {'type': 'string'},
            'password': {'type': 'string'},
            'category': one_or_more({'type': 'string', 'enum': list(CATEGORIES.keys())}, unique_items=True),
            'order_by': {'type': 'string', 'enum': ['seeders', 'leechers', 'time'], 'default': 'time'},
            'order_way': {'type': 'string', 'enum': ['desc', 'asc'], 'default': 'desc'},
            'tags': one_or_more({'type': 'string', 'enum': TAGS}, unique_items=True),
            'all_tags': {'type': 'boolean', 'default': True}
        },
        'required': ['username', 'password'],
        'additionalProperties': False
    }

    base_url = 'https://www.morethan.tv/'

    def get_login_cookie(self, request_session, username, password):
        with Session() as session:
            saved_cookie = session.query(MTVCookie).filter(MTVCookie.username == username).first()
            if saved_cookie and saved_cookie.expires is not None and saved_cookie.expires >= datetime.datetime.now():
                log.debug('Found valid login cookie')
                return saved_cookie.cookie

        url = self.base_url + 'login.php'
        try:
            log.debug('Attempting to retrieve MTV cookie')
            response = request_session.post(url, data={'username': username, 'password': password, 'login': 'Log in',
                                                       'keeplogged': '1'}, timeout=30)
        except RequestException as e:
            raise plugin.PluginError('MTV login failed: %s', e)

        if 'Your username or password was incorrect.' in response.text:
            raise plugin.PluginError('MTV login failed: Your username or password was incorrect.')

        with Session() as session:
            expires = None
            for c in request_session.cookies:
                if c.name == 'session':
                    expires = c.expires
            if expires:
                expires = datetime.datetime.fromtimestamp(expires)
            log.debug('Saving or updating MTV cookie in db')
            cookie = MTVCookie(username=username, cookie=dict(request_session.cookies), expires=expires)
            session.merge(cookie)
            return cookie.cookie

    @plugin.internet(log)
    def search(self, task, entry, config):
        """
            Search for entries on MTV
        """
        cookies = self.get_login_cookie(requests, config['username'], config['password'])

        params = {}

        if 'category' in config:
            categories = config['category'] if isinstance(config['category'], list) else [config['category']]
            for category in categories:
                params[CATEGORIES[category]] = 1

        if 'tags' in config:
            tags = config['tags'] if isinstance(config['tags'], list) else [config['tags']]
            tags = ', '.join(tags)
            params['taglist'] = tags

        entries = set()

        params.update({'tags_type': int(config['all_tags']), 'order_by': config['order_by'], 'search_submit': 1,
                       'order_way': config['order_way'], 'action': 'basic', 'group_results': 0})

        for search_string in entry.get('search_strings', [entry['title']]):
            params['searchstr'] = search_string
            log.debug('Using search params: %s', params)
            try:
                page = requests.get(self.base_url + 'torrents.php', params=params, cookies=cookies)
                log.debug('requesting: %s', page.url)
            except RequestException as e:
                log.error('MTV request failed: %s', e)
                continue

            soup = get_soup(page.content)
            for result in soup.findAll('tr', attrs={'class': 'torrent'}):
                group_info = result.find('td', attrs={'class': 'big_info'}).find('div', attrs={'class': 'group_info'})
                title = group_info.find('a', href=re.compile('torrents.php\?id=\d+')).text
                url = self.base_url + group_info.find('a', href=re.compile('torrents.php\?action=download'))['href']
                torrent_info = result.findAll('td', attrs={'class': 'number_column'})
                size = re.search('(\d+(?:[.,]\d+)*)\s?([KMG]B)', torrent_info[0].text)
                torrent_tags = ', '.join([tag.text for tag in group_info.findAll('div', attrs={'class': 'tags'})])

                e = Entry()

                e['title'] = title
                e['url'] = url
                e['torrent_snatches'] = int(torrent_info[1].text)
                e['torrent_seeds'] = int(torrent_info[2].text)
                e['torrent_leeches'] = int(torrent_info[3].text)
                e['torrent_internal'] = True if group_info.find('span', attrs={'class': 'flag_internal'}) else False
                e['torrent_fast_server'] = True if group_info.find('span', attrs={'class': 'flag_fast'}) else False
                e['torrent_sticky'] = True if group_info.find('span', attrs={'class': 'flag_sticky'}) else False
                e['torrent_tags'] = torrent_tags

                if size:
                    if size.group(2) == 'GB':
                        entry['content_size'] = int(float(size.group(1).replace(',', '')) * 1000 ** 3 / 1024 ** 2)
                    elif size.group(2) == 'MB':
                        entry['content_size'] = int(float(size.group(1).replace(',', '')) * 1000 ** 2 / 1024 ** 2)
                    elif size.group(2) == 'KB':
                        entry['content_size'] = int(float(size.group(1).replace(',', '')) * 1000 / 1024 ** 2)
                    else:
                        entry['content_size'] = int(float(size.group(1).replace(',', '')) / 1024 ** 2)

                entries.add(e)

        return entries


@event('plugin.register')
def register_plugin():
    plugin.register(SearchMTV, 'mtv', groups=['search'], api_ver=2)
