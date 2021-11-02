import time
import codecs
from xml.sax.saxutils import escape
from xml.dom.minidom import parseString

import arrow
from slyguy import plugin, gui, settings, userdata, signals, inputstream
from slyguy.exceptions import PluginError
from slyguy.monitor import monitor
from slyguy.util import replace_kids
from slyguy.drm import is_wv_secure
from slyguy.log import log
from slyguy.constants import MIDDLEWARE_PLUGIN

from .language import _
from .api import API
from .config import Config
from .mvpd import MVPD

api = API()
config = Config()

@signals.on(signals.BEFORE_DISPATCH)
def before_dispatch():
    config.init()
    api.new_session(config)
    plugin.logged_in = api.logged_in

@plugin.route('')
def home(**kwargs):
    folder = plugin.Folder(cacheToDisc=False)

    if not api.logged_in:
        folder.add_item(label=_(_.LOGIN, _bold=True), path=plugin.url_for(login))
    else:
        if config.has_featured():
            folder.add_item(label=_(_.FEATURED, _bold=True), path=plugin.url_for(featured))

        folder.add_item(label=_(_.SHOWS, _bold=True), path=plugin.url_for(shows))

        if config.has_movies():
            folder.add_item(label=_(_.MOVIES, _bold=True), path=plugin.url_for(movies))

        if config.has_live_tv():
            folder.add_item(label=_(_.LIVE_TV, _bold=True), path=plugin.url_for(live_tv))

        if config.has_brands():
            folder.add_item(label=_(_.BRANDS, _bold=True), path=plugin.url_for(brands))

        if config.has_news():
            folder.add_item(label=_(_.NEWS, _bold=True), path=plugin.url_for(news))

        folder.add_item(label=_(_.SEARCH, _bold=True), path=plugin.url_for(search))

        if settings.getBool('bookmarks', True):
            folder.add_item(label=_(_.BOOKMARKS, _bold=True),  path=plugin.url_for(plugin.ROUTE_BOOKMARKS), bookmark=False)

        if config.has_profiles():
            folder.add_item(label=_.SELECT_PROFILE, path=plugin.url_for(select_profile), art={'thumb': config.image(userdata.get('profile_img'))}, info={'plot': userdata.get('profile_name')}, _kiosk=False, bookmark=False)

        folder.add_item(label=_.LOGOUT, path=plugin.url_for(logout), _kiosk=False, bookmark=False)

    folder.add_item(label=_.SETTINGS, path=plugin.url_for(plugin.ROUTE_SETTINGS), _kiosk=False, bookmark=False)

    return folder

@plugin.route()
def login(**kwargs):
    if not config.init(fresh=True):
        raise PluginError(_.OUT_OF_REGION)

    api.new_session(config)
    device_link = config.has_device_link()
    mvpd = config.has_mvpd()

    options = []

    if config.has_device_link():
        options.append([_.DEVICE_CODE, _device_code])

    if config.has_mvpd():
        options.append([_.PARTNER_LOGIN, _partner_login])

    options.append([_.EMAIL_PASSWORD, _email_password])

    index = 0 if len(options) == 1 else gui.context_menu([x[0] for x in options])
    if index == -1 or not options[index][1]():
        return

    config.save()
    if config.has_profiles():
        _select_profile()

    gui.refresh()

def _partner_login():
    mpvd = MVPD(config)

    options = []
    providers = mpvd.providers()
    for provider in providers:
        options.append(plugin.Item(label=provider['primaryName'], art={'thumb': provider['pickerLogoUrl']}))

    index = gui.select(_.SELECT_PARTNER, options=options, useDetails=True)
    if index == -1:
        return

    provider = providers[index]
    code, url = mpvd.code(provider)
    start = time.time()
    max_time = 600
    last_check = start

    thread = mpvd.wait_login(code)

    with gui.progress(_(_.DEVICE_LINK_STEPS, url=url, code=code), heading=_.PARTNER_LOGIN) as progress:
        while (time.time() - start) < max_time:
            if progress.iscanceled() or monitor.waitForAbort(1):
                return

            progress.update(int(((time.time() - start) / max_time) * 100))
            if not thread.is_alive():
                token = mpvd.authorize()
                api.mvpd_login(provider, token)
                return True

def _email_password():
    username = gui.input(_.ASK_EMAIL, default=userdata.get('username', '')).strip()
    if not username:
        return

    userdata.set('username', username)

    password = gui.input(_.ASK_PASSWORD, hide_input=True).strip()
    if not password:
        return

    api.login(username, password)

    return True

def _device_code():
    start = time.time()
    data = api.device_code()

    poll_time = int(data['retryInterval']/1000)
    max_time = int(data['retryDuration']/1000)
    device_token = data['deviceToken']
    code = data['activationCode']

    with gui.progress(_(_.DEVICE_LINK_STEPS, url=config.device_link_url, code=code), heading=_.DEVICE_CODE) as progress:
        while (time.time() - start) < max_time:
            for i in range(poll_time):
                if progress.iscanceled() or monitor.waitForAbort(1):
                    return

                progress.update(int(((time.time() - start) / max_time) * 100))

            result = api.device_login(code, device_token)
            if result:
                return True

            elif result == -1:
                return False

@plugin.route()
def select_profile(**kwargs):
    _select_profile()
    gui.refresh()

def _select_profile():
    profiles = api.user()['accountProfiles']

    values = []
    options = []
    default = -1
    for index, profile in enumerate(profiles):
        values.append(profile['id'])
        options.append(plugin.Item(label=profile['name'], art={'thumb': config.image(profile['profilePicPath'])}))
        if profile['id'] == userdata.get('profile_id'):
            default = index

    index = gui.select(_.SELECT_PROFILE, options=options, preselect=default, useDetails=True)
    if index < 0:
        return

    api.set_profile(values[index])
    gui.notification(_.PROFILE_ACTIVATED, heading=userdata.get('profile_name'), icon=config.image(userdata.get('profile_img')))

@plugin.route()
def featured(slug=None, **kwargs):
    folder = plugin.Folder(_.FEATURED)

    for row in api.featured():
        if slug is None:
            if row['apiParams']['name'] in ('Keep+Watching', 'My+List', 'On+Now'): # TO DO
                continue

            folder.add_item(
                label = row['title'],
                path = plugin.url_for(featured, slug=row['apiParams']['name']),
            )
            continue

        if slug != row['apiParams']['name']:
            continue

        for row in api.carousel(row['apiBaseUrl'], params=row['apiParams']):
            if row.get('showId'):
                folder.add_item(
                    label = row['showTitle'],
                    info = {
                        'plot': row['about'],
                        'mediatype': 'tvshow',
                    },
                    art = _show_art(row['showAssets']),
                    path = plugin.url_for(show, show_id=row['showId']),
                )

            elif row.get('movieContent'):
                data = row['movieContent']
                folder.add_item(
                    label = data['label'].strip() or data['title'].strip(),
                    info = {
                        'plot': data.get('shortDescription', data['description']),
                        'aired': data['_airDateISO'],
                        'dateadded': data['_pubDateISO'],
                        'genre': data['genre'],
                        'duration': data['duration'],
                        'mediatype': 'movie',
                        'trailer': plugin.url_for(play, video_id=row['trailerContentId']) if row.get('trailerContentId') else None,
                    },
                    art = _movie_art(data['thumbnailSet']),
                    path = plugin.url_for(play, video_id=data['contentId']),
                    playable = True,
                )

        break

    return folder

@plugin.route()
def movies(genre=None, title=None, page=1, **kwargs):
    folder = plugin.Folder(title or _.MOVIES)
    page = int(page)
    num_results = 50

    if genre is None:
        folder.add_item(
            label = _.POPULAR,
            path = plugin.url_for(movies, genre='popular', title=_.POPULAR),
        )

        folder.add_item(
            label = _.A_Z,
            path = plugin.url_for(movies, genre='', title=_.A_Z),
        )

        for row in api.movie_genres():
            if row['slug'] in ('popular', 'a-z'):
                continue

            folder.add_item(
                label = row['title'],
                path = plugin.url_for(movies, genre=row['slug'], title=row['title']),
            )

        return folder

    if genre == 'popular':
        data = api.trending_movies()
        data['movies'] = [x['content'] for x in data['trending'] if x['content_type'] == 'movie']
        data['numFound'] = len(data['movies'])
    else:
        data = api.movies(genre=genre, num_results=num_results, page=page)

    folder.title += ' ({})'.format(data['numFound'])

    for row in data['movies']:
        data = row['movieContent']
        folder.add_item(
            label = data['label'].strip() or data['title'].strip(),
            info = {
                'plot': data.get('shortDescription', data['description']),
                'aired': data['_airDateISO'],
                'dateadded': data['_pubDateISO'],
                'genre': data['genre'],
                'duration': data['duration'],
                'mediatype': 'movie',
                'trailer': plugin.url_for(play, video_id=row['movie_trailer_id']) if row.get('movie_trailer_id') else None,
            },
            art = _movie_art(data['thumbnailSet']),
            path = plugin.url_for(play, video_id=data['contentId']),
            playable = True,
        )

    if len(folder.items) == num_results:
        folder.add_item(
            label = _(_.NEXT_PAGE, page=page+1),
            path = plugin.url_for(movies, genre=genre, title=title, page=page+1),
            specialsort = 'bottom',
        )

    return folder

@plugin.route()
def shows(group_id=None, **kwargs):
    if group_id is None:
        folder = plugin.Folder(_.SHOWS)

        for row in api.show_groups():
            folder.add_item(
                label = row['title'],
                path = plugin.url_for(shows, group_id=row['id']),
            )

        return folder

    data = api.show_group(group_id)

    folder = plugin.Folder(data['title'] + ' ({})'.format(data['totalShowGroupCount']))
    items = _process_shows(data['showGroupItems'])
    folder.add_items(items)

    return folder

def _process_shows(rows):
    items = []

    for row in rows:
        plot = _(_.EPISODE_COUNT, count=row['episodeVideoCount']['totalEpisodes'])
        # if row['episodeVideoCount']['totalClips']:
        #     plot += '\n'+ _(_.CLIPS_COUNT, count=row['episodeVideoCount']['totalClips'])

        item = plugin.Item(
            label = row['title'],
            info = {
                'genre': row['category'],
                'mediatype': 'tvshow',
                'plot': plot,
            },
            art = _show_art(row['showAssets']),
            path = plugin.url_for(show, show_id=row['showId']),
        )

        items.append(item)

    return items

@plugin.route()
def show(show_id, **kwargs):
    show = api.show(show_id)
    art = _show_art(show['showAssets'])

    folder = plugin.Folder(show['show']['results'][0]['title'], thumb=art.get('thumb'), fanart=art.get('fanart'))

    plot = show['show']['results'][0]['about'] + '\n\n'

    clip_count = 0
    for row in sorted(api.seasons(show_id), key=lambda x: int(x['seasonNum'])):
        clip_count += row['clipsCount']
        if not row['totalCount']:
            continue

        folder.add_item(
            label = _(_.SEASON, season=row['seasonNum']),
            info = {
                'plot': plot + _(_.EPISODE_COUNT, count=row['totalCount']),
                'mediatype': 'season',
                'tvshowtitle': show['show']['results'][0]['title'],
            },
            path = plugin.url_for(season, show_id=show_id, season=row['seasonNum']),
        )

    # if clip_count:
    #     folder.add_item(
    #         label = _.CLIPS,
    #         info = {
    #             'plot': plot + _(_.CLIPS_COUNT, count=clip_count),
    #         }
    #     )

    return folder

@plugin.route()
def season(show_id, season, **kwargs):
    show = api.show(show_id)
    art = _show_art(show['showAssets'])

    folder = plugin.Folder(show['show']['results'][0]['title'], fanart=art.get('fanart'))

    for row in api.episodes(show_id, season):
        folder.add_item(
            label = row['label'].strip() or row['title'].strip(),
            info = {
                'aired': row['_airDateISO'],
                'dateadded': row['_pubDateISO'],
                'plot': row['shortDescription'],
                'season': row['seasonNum'],
                'episode': row['episodeNum'],
                'duration': row['duration'],
                'genre': row['topLevelCategory'],
                'mediatype': 'episode',
                'tvshowtitle': show['show']['results'][0]['title'],
            },
            art = {'thumb': config.thumbnail(row['thumbnail'])},
            path = plugin.url_for(play, video_id=row['contentId']),
            playable = True,
        )

    return folder

@plugin.route()
def live_tv(**kwargs):
    folder = plugin.Folder(_.LIVE_TV)

    now = arrow.utcnow()

    for row in api.live_channels():
        if not row['currentListing']:
            continue

        for listing in row['currentListing']:
            start = arrow.get(listing['startTimestamp'])
            plot = u'[{}] {}\n'.format(start.to('local').format('h:mma'), listing['title'])

            folder.add_item(
                label = row['channelName'].strip(),
                info = {
                    'plot': plot,
                },
                art = {'thumb': config.image(row['filePathLogoSelected'])},
                path = plugin.url_for(play_channel, slug=row['slug'], listing_id=listing['id'] if row['streamType'] == 'mpx_live' else None, _is_live=True),
                playable = True,
            )

    return folder

def _show_art(assets):
    art = {}

    if 'filepath_show_browse_poster' in assets:
        art['thumb'] = config.image(assets['filepath_show_browse_poster'])

    if 'filepath_brand_hero' in assets:
        art['fanart'] = config.image(assets['filepath_brand_hero'], 'w1920-q80')

    return art

def _get_thumb(thumbs, _type='PosterArt', dimensions='w400'):
    if not thumbs:
        return None

    for row in thumbs:
        if row['assetType'] == _type:
            return config.thumbnail(row['url'], dimensions)

    return None

def _movie_art(thumbs):
    art = {
        'thumb': _get_thumb(thumbs, 'PosterArt'),
        'fanart': _get_thumb(thumbs, 'Thumbnail', 'w1920-q80'),
    }
    return art

@plugin.route()
@plugin.search()
def search(query, page, **kwargs):
    items = []

    for row in api.search(query):
        if row['term_type'] == 'show':
            items.append(plugin.Item(
                label = row['title'],
                info = {
                    'mediatype': 'tvshow',
                },
                art = _show_art(row['showAssets']),
                path = plugin.url_for(show, show_id=row['show_id']),
            ))

        elif row['term_type'] == 'movie':
            data = row['videoList']['itemList'][0]

            try: aired = str(arrow.get(data['_airDate'], 'MM/DD/YY'))
            except: aired = None

            items.append(plugin.Item(
                label = data['label'].strip() or data['title'].strip(),
                info = {
                    'plot': data.get('shortDescription', data['description']),
                    'aired': aired,
                    'duration': data['duration'],
                    'mediatype': 'movie',
                    'trailer': plugin.url_for(play, video_id=row['movie_trailer_id']) if row.get('movie_trailer_id') else None,
                },
                art = _movie_art(data['thumbnailSet']),
                path = plugin.url_for(play, video_id=data['contentId']),
                playable = True,
            ))

    return items, False

def _parse_item(row):
    if row['mediaType'] == 'Standalone':
        row['mediaType'] = 'Movie'
    elif row['mediaType'] == 'Clip':
        row['mediaType'] = 'Trailer'

    if row['mediaType'] in ('Movie', 'Trailer'):
        return plugin.Item(
            label = row['title'],
            info = {
                'aired': row['_airDateISO'],
                'dateadded': row['_pubDateISO'],
                'genre': row['genre'],
                'plot': row['shortDescription'],
                'duration': row['duration'],
                'mediatype': 'movie' if row['mediaType'] == 'Movie' else 'video',
            },
            art = _movie_art(row['thumbnailSet']) if row['mediaType'] == 'Movie' else {'thumb': _get_thumb(row['thumbnailSet'], 'Thumbnail')},
        )

    return plugin.Item()

@plugin.route()
@plugin.plugin_middleware()
def mpd_request(_data, _path, **kwargs):
    root = parseString(_data)

    dolby_vison = settings.getBool('dolby_vision', False)
    enable_4k = settings.getBool('4k_enabled', True)
    h265 = enable_4k or settings.getBool('h265', False)
    enable_ac3 = settings.getBool('ac3_enabled', False)
    enable_ec3 = settings.getBool('ec3_enabled', False)
    enable_atmos = settings.getBool('atmos_enabled', False)

    vid_sets = []
    for adap_set in root.getElementsByTagName('AdaptationSet'):
        if adap_set.getAttribute('contentType') == 'video':
            kid = None
            for elem in adap_set.getElementsByTagName('ContentProtection'):
                _kid = elem.getAttribute('cenc:default_KID')
                if _kid:
                    kid = _kid
                    break

            vid_sets.append([int(adap_set.getAttribute('maxHeight') or 0), kid, adap_set])

        for elem in adap_set.getElementsByTagName('Representation'):
            parent = elem.parentNode
            codecs = elem.getAttribute('codecs').lower()
            height = int(elem.getAttribute('height') or 0)
            width = int(elem.getAttribute('width') or 0)

            if not dolby_vison and (codecs.startswith('dvh1') or codecs.startswith('dvhe')):
                parent.removeChild(elem)

            elif not h265 and (codecs.startswith('hvc') or codecs.startswith('hev')):
                parent.removeChild(elem)

            elif not enable_4k and (height > 1080 or width > 1920):
                parent.removeChild(elem)

            elif not enable_ac3 and codecs == 'ac-3':
                parent.removeChild(elem)

            elif (not enable_ec3 or not enable_atmos) and codecs == 'ec-3':
                is_atmos = False
                for supelem in elem.getElementsByTagName('SupplementalProperty'):
                    if supelem.getAttribute('value') == 'JOC':
                        is_atmos = True
                        break

                if not enable_ec3 or (not enable_atmos and is_atmos):
                    parent.removeChild(elem)

    vid_sets = sorted(vid_sets, key=lambda x: x[0], reverse=True)
    if vid_sets and not is_wv_secure():
        lowest = vid_sets.pop()
        for row in vid_sets:
            if row[1] != lowest[1]:
                row[2].parentNode.removeChild(row[2])

    ## Remove empty adaption sets
    for adap_set in root.getElementsByTagName('AdaptationSet'):
        if not adap_set.getElementsByTagName('Representation'):
            adap_set.parentNode.removeChild(adap_set)
    #################

    ## Fix of cenc pssh to only contain kids still present
    kids = []
    for elem in root.getElementsByTagName('ContentProtection'):
        kids.append(elem.getAttribute('cenc:default_KID'))

    if kids:
        for elem in root.getElementsByTagName('ContentProtection'):
            if elem.getAttribute('schemeIdUri') == 'urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed':
                for elem2 in elem.getElementsByTagName('cenc:pssh'):
                    current_cenc = elem2.firstChild.nodeValue
                    new_cenc = replace_kids(current_cenc, kids, version0=True)
                    if current_cenc != new_cenc:
                        elem2.firstChild.nodeValue = new_cenc
                        log.debug('Dash Fix: cenc:pssh {} -> {}'.format(current_cenc, new_cenc))
    ################################################

    with open(_path, 'wb') as f:
        f.write(root.toprettyxml(encoding='utf-8'))

@plugin.route()
@plugin.login_required()
def play(video_id, **kwargs):
    url, license_url, token, data = api.play(video_id)

    item = _parse_item(data)
    item.proxy_data['middleware'] = {url: {'type': MIDDLEWARE_PLUGIN, 'url': plugin.url_for(mpd_request)}}

    headers = {
        'authorization': 'Bearer {}'.format(token),
    }

    item.update(
        path = url,
        headers = headers,
        inputstream = inputstream.Widevine(
            license_key = license_url,
        ),
    )

    return item

@plugin.route()
@plugin.login_required()
def play_channel(slug, listing_id=None, **kwargs):
    channels = api.live_channels()

    headers = {}

    for row in channels:
        if row['slug'] != slug:
            continue

        play_path = None
        if row['dma']:
            play_path = row['dma']['playback_url']
        elif row['currentListing']:
            if not listing_id:
                selected = row['currentListing'][0]
            else:
                for listing in row['currentListing']:
                    if str(listing['id']) == listing_id:
                        selected = listing
                        break

            if selected:
                if selected['contentCANVideo'].get('liveStreamingUrl'):
                    play_path = selected['contentCANVideo']['liveStreamingUrl']
                elif selected['streamType'] == 'mpx_live':
                    play_path, license_url, token, data = api.play(selected['videoContentId'])
                    headers['authorization'] = 'Bearer {}'.format(token)

        if not play_path:
            raise PluginError('No url found for this channel')

        return plugin.Item(
            label = row['channelName'],
            info = {
                'plot': row['description'],
            },
            headers = headers,
            art = {'thumb': config.image(row['filePathLogoSelected'])},
            path = play_path,
            inputstream = inputstream.HLS(live=True),
        )

    raise PluginError('Unable to find that channel')

@plugin.route()
def logout(**kwargs):
    if not gui.yes_no(_.LOGOUT_YES_NO):
        return

    api.logout()
    config.clear()
    gui.refresh()

@plugin.route()
@plugin.merge()
def playlist(output, **kwargs):
    with codecs.open(output, 'w', encoding='utf8') as f:
        f.write(u'#EXTM3U\n')

        for row in api.live_channels():
            if not row['currentListing'] or row['streamType'] == 'mpx_live':
                continue

            f.write(u'#EXTINF:-1 tvg-id="{id}" tvg-name="{name}" tvg-logo="{logo}",{name}\n{path}\n'.format(
                id=row['slug'], name=row['channelName'], logo=config.image(row['filePathLogoSelected']), path=plugin.url_for(play_channel, slug=row['slug'], _is_live=True)))

@plugin.route()
@plugin.merge()
def epg(output, **kwargs):
    channels = api.live_channels()
    now = arrow.now()
    until = now.shift(days=settings.getInt('epg_days', 3))

    with codecs.open(output, 'w', encoding='utf8') as f:
        f.write(u'<?xml version="1.0" encoding="utf-8" ?><tv>')

        for channel in api.live_channels():
            if not channel['currentListing'] or (not channel['dma'] and not channel['currentListing'][-1]['contentCANVideo'].get('liveStreamingUrl')):
                continue

            f.write(u'<channel id="{id}"></channel>'.format(id=channel['slug']))

            page = 1
            stop = now
            while stop < until:
                rows = api.epg(channel['slug'], rows=100, page=page)
                page += 1
                if not rows:
                    break

                for row in rows:
                    start = arrow.get(row['startTimestamp'])
                    stop = arrow.get(row['endTimestamp'])

                    icon = u'<icon src="{}"/>'.format(config.image(row['filePathThumb'])) if row['filePathThumb'] else ''
                    desc = u'<desc>{}</desc>'.format(escape(row['description'])) if row['description'] else ''

                    f.write(u'<programme channel="{id}" start="{start}" stop="{stop}"><title>{title}</title>{desc}{icon}</programme>'.format(
                        id=channel['slug'], start=start.format('YYYYMMDDHHmmss Z'), stop=stop.format('YYYYMMDDHHmmss Z'), title=escape(row['title']), desc=desc, icon=icon,
                    ))

        f.write(u'</tv>')
