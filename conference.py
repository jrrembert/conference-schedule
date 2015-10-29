#!/usr/bin/env python

"""
conference.py -- Udacity conference server-side Python App Engine API;
    uses Google Cloud Endpoints

$Id: conference.py,v 1.25 2014/05/24 23:42:19 wesc Exp wesc $

created by wesc on 2014 apr 21

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'


import ast
from datetime import datetime
import json
import os
import time

import endpoints
from protorpc import messages
from protorpc import message_types
from protorpc import remote

from google.appengine.api import memcache
from google.appengine.api import taskqueue
from google.appengine.api import urlfetch
from google.appengine.ext import ndb

from models import ConflictException
from models import Profile, ProfileMiniForm, ProfileForm, ProfileWishListForm
from models import StringMessage, BooleanMessage
from models import Conference, ConferenceForm, ConferenceForms, ConferenceFeaturedSpeakerForm
from models import ConferenceQueryForm, ConferenceQueryForms
from models import SessionQueryForm, SessionQueryForms
from models import Session, SessionForm, SessionForms
from models import TeeShirtSize

from settings import WEB_CLIENT_ID
from settings import ANDROID_CLIENT_ID
from settings import IOS_CLIENT_ID
from settings import ANDROID_AUDIENCE

EMAIL_SCOPE = endpoints.EMAIL_SCOPE
API_EXPLORER_CLIENT_ID = endpoints.API_EXPLORER_CLIENT_ID
MEMCACHE_ANNOUNCEMENTS_KEY = "RECENT_ANNOUNCEMENTS"

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

DEFAULTS = {
    "city": "Default City",
    "maxAttendees": 0,
    "seatsAvailable": 0,
    "topics": [ "Default", "Topic" ],
    "featured_speakers": ["TBA"]
}

SESSION_DEFAULTS = {
    "highlights": "None set",
    "speakers": [u"TBA"],
    "duration": "",
    "typeOfSession": "None set",
    "start_time": 0
}

OPERATORS = {
            'EQ':   '=',
            'GT':   '>',
            'GTEQ': '>=',
            'LT':   '<',
            'LTEQ': '<=',
            'NE':   '!='
            }

FIELDS =    {
            'CITY': 'city',
            'TOPIC': 'topics',
            'MONTH': 'month',
            'MAX_ATTENDEES': 'maxAttendees',
            'FEATURED_SPEAKERS': 'featured_speakers'
            }

SESSION_FIELDS = {
    'NAME': 'name',
    'HIGHLIGHTS': 'highlights',
    'SPEAKERS': 'speakers',
    'DURATION': 'duration',
    'TYPEOFSESSION': 'typeOfSession',
    'DATE': 'date',
    'START_TIME': 'start_time',
}

CONF_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
)

CONF_POST_REQUEST = endpoints.ResourceContainer(
    ConferenceForm,
    websafeConferenceKey=messages.StringField(1),
)

SESSION_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1),
    typeOfSession=messages.StringField(2),
    speaker=messages.StringField(3)
    )

SESSION_POST_REQUEST = endpoints.ResourceContainer(
    SessionForm,
    websafeConferenceKey=messages.StringField(1),
    )

WISHLIST_POST_REQUEST = endpoints.ResourceContainer(
    ProfileWishListForm,
    websafeSessionKey=messages.StringField(1)
    )

FEATURED_SPEAKER_GET_REQUEST = endpoints.ResourceContainer(
    message_types.VoidMessage,
    websafeConferenceKey=messages.StringField(1)
    )

# - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

def _getUserId():
    """A workaround implementation for getting userid."""
    auth = os.getenv('HTTP_AUTHORIZATION')
    bearer, token = auth.split()
    token_type = 'id_token'
    if 'OAUTH_USER_ID' in os.environ:
        token_type = 'access_token'
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?%s=%s'
           % (token_type, token))
    user = {}
    wait = 1
    for i in range(3):
        resp = urlfetch.fetch(url)
        if resp.status_code == 200:
            user = json.loads(resp.content)
            break
        elif resp.status_code == 400 and 'invalid_token' in resp.content:
            url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?%s=%s'
                   % ('access_token', token))
        else:
            time.sleep(wait)
            wait = wait + i
    return user.get('user_id', '')


@endpoints.api(name='conference', version='v1', audiences=[ANDROID_AUDIENCE],
    allowed_client_ids=[WEB_CLIENT_ID, API_EXPLORER_CLIENT_ID, ANDROID_CLIENT_ID, IOS_CLIENT_ID],
    scopes=[EMAIL_SCOPE])
class ConferenceApi(remote.Service):
    """Conference API v0.1"""

# - - - Conference objects - - - - - - - - - - - - - - - - -

    def _copyConferenceToForm(self, conf, displayName):
        """Copy relevant fields from Conference to ConferenceForm."""
        cf = ConferenceForm()
        for field in cf.all_fields():
            if hasattr(conf, field.name):
                # convert Date to date string; just copy others
                if field.name.endswith('Date'):
                    setattr(cf, field.name, str(getattr(conf, field.name)))
                else:
                    setattr(cf, field.name, getattr(conf, field.name))
            elif field.name == "websafeKey":
                setattr(cf, field.name, conf.key.urlsafe())
        if displayName:
            setattr(cf, 'organizerDisplayName', displayName)
        cf.check_initialized()
        return cf


    def _createConferenceObject(self, request):
        """Create or update Conference object, returning ConferenceForm/request."""
        # preload necessary data items
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = _getUserId()

        if not request.name:
            raise endpoints.BadRequestException("Conference 'name' field required")

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['websafeKey']
        del data['organizerDisplayName']

        # add default values for those missing (both data model & outbound Message)
        for df in DEFAULTS:
            if data[df] in (None, []):
                data[df] = DEFAULTS[df]
                setattr(request, df, DEFAULTS[df])

        # convert dates from strings to Date objects; set month based on start_date
        if data['startDate']:
            data['startDate'] = datetime.strptime(data['startDate'][:10], "%Y-%m-%d").date()
            data['month'] = data['startDate'].month
        else:
            data['month'] = 0
        if data['endDate']:
            data['endDate'] = datetime.strptime(data['endDate'][:10], "%Y-%m-%d").date()

        # set seatsAvailable to be same as maxAttendees on creation
        if data["maxAttendees"] > 0:
            data["seatsAvailable"] = data["maxAttendees"]
        # generate Profile Key based on user ID and Conference
        # ID based on Profile key get Conference key from ID
        p_key = ndb.Key(Profile, user_id)
        c_id = Conference.allocate_ids(size=1, parent=p_key)[0]
        c_key = ndb.Key(Conference, c_id, parent=p_key)
        data['key'] = c_key
        data['organizerUserId'] = request.organizerUserId = user_id

        # create Conference, send email to organizer confirming
        # creation of Conference & return (modified) ConferenceForm
        Conference(**data).put()
        taskqueue.add(params={'email': user.email(),
            'conferenceInfo': repr(request)},
            url='/tasks/send_conference_confirmation_email'
        )
        return request


    @ndb.transactional()
    def _updateConferenceObject(self, request):
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = _getUserId()

        # copy ConferenceForm/ProtoRPC Message into dict
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}

        # update existing conference
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        # check that conference exists
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)

        # check that user is owner
        if user_id != conf.organizerUserId:
            raise endpoints.ForbiddenException(
                'Only the owner can update the conference.')

        # Not getting all the fields, so don't create a new object; just
        # copy relevant fields from ConferenceForm to Conference object
        for field in request.all_fields():
            data = getattr(request, field.name)
            # only copy fields where we get data
            if data not in (None, []):
                # special handling for dates (convert string to Date)
                if field.name in ('startDate', 'endDate'):
                    data = datetime.strptime(data, "%Y-%m-%d").date()
                    if field.name == 'startDate':
                        conf.month = data.month
                # write to Conference object
                setattr(conf, field.name, data)
        conf.put()
        prof = ndb.Key(Profile, user_id).get()
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(ConferenceForm, ConferenceForm, path='conference',
            http_method='POST', name='createConference')
    def createConference(self, request):
        """Create new conference."""
        return self._createConferenceObject(request)


    @endpoints.method(CONF_POST_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='PUT', name='updateConference')
    def updateConference(self, request):
        """Update conference w/provided fields & return w/updated info."""
        return self._updateConferenceObject(request)


    @endpoints.method(CONF_GET_REQUEST, ConferenceForm,
            path='conference/{websafeConferenceKey}',
            http_method='GET', name='getConference')
    def getConference(self, request):
        """Return requested conference (by websafeConferenceKey)."""
        # get Conference object from request; bail if not found
        conf = ndb.Key(urlsafe=request.websafeConferenceKey).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        prof = conf.key.parent().get()
        # return ConferenceForm
        return self._copyConferenceToForm(conf, getattr(prof, 'displayName'))


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='getConferencesCreated',
            http_method='POST', name='getConferencesCreated')
    def getConferencesCreated(self, request):
        """Return conferences created by user."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # create ancestor query for all key matches for this user
        confs = Conference.query(ancestor=ndb.Key(Profile, _getUserId()))
        
        prof = ndb.Key(Profile, _getUserId()).get()
        # return set of ConferenceForm objects per Conference
        return ConferenceForms(
            items=[self._copyConferenceToForm(conf, getattr(prof, 'displayName')) for conf in confs]
        )


    def _getQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Conference.query()
        inequality_filter, filters = self._formatFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Conference.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Conference.name)

        for filtr in filters:
            if filtr["field"] in ["month", "maxAttendees"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)
        return q


    def _formatFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)
        return (inequality_field, formatted_filters)


    @endpoints.method(ConferenceQueryForms, ConferenceForms,
            path='queryConferences',
            http_method='POST',
            name='queryConferences')
    def queryConferences(self, request):
        """Query for conferences."""
        conferences = self._getQuery(request)

        # need to fetch organiser displayName from profiles
        # get all keys and use get_multi for speed
        organisers = [(ndb.Key(Profile, conf.organizerUserId)) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return individual ConferenceForm object per Conference
        return ConferenceForms(
                items=[self._copyConferenceToForm(conf, names[conf.organizerUserId]) for conf in \
                conferences]
        )


# - - - Session objects - - - - - - - - - - - - - - - - - - -

    def _copySessionToForm(self, session, displayName):
        """Copy relevant fields from Session to SessionForm."""
        sf = SessionForm()
        for field in sf.all_fields():
            if hasattr(session, field.name):
                # convert DateTime to datetime string; just copy others
                if field.name == 'date':
                    setattr(sf, field.name, str(getattr(session, field.name)))
                else:
                    setattr(sf, field.name, getattr(session, field.name))
        
        if displayName:
            setattr(sf, 'organizer_display_name', displayName)
        sf.check_initialized()
        return sf


    def _createSessionObject(self, request):
        """Create a Session object, returning a SessionForm request."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = _getUserId()

        profile = ndb.Key(Profile, _getUserId()).get()

        if not request.name:
            raise endpoints.BadRequestException("Session 'name' field required")
    
        data = {field.name: getattr(request, field.name) for field in request.all_fields()}
        del data['organizer_display_name']

        # add default values for those missing (both data model & outbound Message)
        for df in SESSION_DEFAULTS:
            if data[df] in (None, []):
                data[df] = SESSION_DEFAULTS[df]
                setattr(request, df, SESSION_DEFAULTS[df])

        if not isinstance(request.start_time, int) or not (0 <= request.start_time <= 24):
            raise endpoints.BadRequestException("Session 'start_time' must be an integer from 0 to 23.")

        # convert dates from strings to Date objects; set month based on start_date
        if data['date']:
            data['date'] = datetime.strptime(data['date'][11:16], "%H:%M").date()
            data['start_time'] = data['date'].hour
        
        conf_key = ndb.Key(urlsafe=request.websafeConferenceKey).get().key
        session_id = Session.allocate_ids(size=1, parent=conf_key)[0]
        session_key = ndb.Key(Session, session_id, parent=conf_key)
        data['key'] = session_key
        data['organizer_user_id'] = user_id

        Session(**data).put()

        # Update speaker info in memcache
        taskqueue.add(params={'speakers': repr(data['speakers']),
            'websafeConferenceKey': data['websafeConferenceKey']},
            url='/tasks/set_featured_speaker')

        # Send email confirmation of session creation
        taskqueue.add(params={'email': user.email,
            'sessionInfo': repr(request)},
            url='/tasks/send_session_confirmation_email'
            )
        
        return self._copySessionToForm(request, getattr(profile, 'displayName'))


    @endpoints.method(SESSION_GET_REQUEST, SessionForms,
        path='conference/{websafeConferenceKey}/sessions',
        http_method='GET', name='getConferenceSessions')
    def getConferenceSessions(self, request):
        """Return sessions for a given conference."""
        conference = ndb.Key(urlsafe=request.websafeConferenceKey).get()

        if not conference:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        
        return SessionForms(
            items=[self._copySessionToForm(session, getattr(conference, 'organizerUserId')) for session in sessions])


    @endpoints.method(SESSION_GET_REQUEST, SessionForms,
        path='conference/{websafeConferenceKey}/sessions/{typeOfSession}',
        http_method='GET', name='getConferenceSessionsByType')
    def getConferenceSessionsByType(self, request):
        """Find sessions of a specific type for a given conference."""
        conference = ndb.Key(urlsafe=request.websafeConferenceKey).get()

        if not conference:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
        
        sessions = Session.query(
            Session.typeOfSession == request.typeOfSession, 
            ancestor=conference.key)

        return SessionForms(
            items=[self._copySessionToForm(session, getattr(conference, 'organizerUserId')) for session in sessions])


    @endpoints.method(SESSION_GET_REQUEST, SessionForms,
        path='conference/{websafeConferenceKey}/sessions/{speaker}',
        http_method='GET', name='getConferenceSessionsBySpeaker')
    def getSessionsBySpeaker(self, request):
        """Find sessions featuring a specific speaker for a given conference."""
        conference = ndb.Key(urlsafe=request.websafeConferenceKey).get()

        if not conference:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % request.websafeConferenceKey)
  
        sessions = Session.query(
            Session.speakers.IN([request.speaker]),
            ancestor=conference.key)

        return SessionForms(
            items=[self._copySessionToForm(session, getattr(conference, 'organizerUserId')) for session in sessions])


    @endpoints.method(SESSION_POST_REQUEST, SessionForm,
        path='conference/{websafeConferenceKey}/sessions',
        http_method='POST', name='createSession')
    def createSession(self, request):
        """Create a session for a given conference."""
        return self._createSessionObject(request)


    @endpoints.method(SessionQueryForms, SessionForms,
        path='querySessions',
        http_method='POST',
        name='querySessions')
    def querySessions(self, request):
        """Query for sessions."""
        sessions = self._getSessionQuery(request)

        # Fetch organizer displayName from profiles
        organizers = [ndb.Key(Profile, session.organizer_user_id) for session in sessions]
        profiles = ndb.get_multi(organizers)

        display_names = {}
        for profile in profiles:
            display_names[profile.key.id()] = profile.displayName

        return SessionForms(
            items=[self._copySessionToForm(session, display_names[session.organizer_user_id]) for session in sessions])


    @endpoints.method(SessionQueryForms, SessionForms,
        path='querySessionsSpecial',
        http_method='POST',
        name='querySessionsSpecial')
    def querySessionsSpecial(self, request):
        """Return sessions after 7 PM that are not workshops."""
        session_query = Session.query()

        sessions = []

        sessions_after_seven_pm = Session.query(Session.start_time >= 18)
        
        for session in sessions_after_seven_pm:
            if session.typeOfSession != 'Workshop':
                sessions.append(session)

        # Fetch organizer displayName from profiles
        organizers = [ndb.Key(Profile, session.organizer_user_id) for session in sessions]
        profiles = ndb.get_multi(organizers)

        display_names = {}
        for profile in profiles:
            display_names[profile.key.id()] = profile.displayName

        return SessionForms(
            items=[self._copySessionToForm(session, display_names[session.organizer_user_id]) for session in sessions])


    def _getSessionQuery(self, request):
        """Return formatted query from the submitted filters."""
        q = Session.query()
        
        inequality_filter, filters = self._formatSessionFilters(request.filters)

        # If exists, sort on inequality filter first
        if not inequality_filter:
            q = q.order(Session.name)
        else:
            q = q.order(ndb.GenericProperty(inequality_filter))
            q = q.order(Session.name)

        for filtr in filters:
            if filtr["field"] in ["start_time"]:
                filtr["value"] = int(filtr["value"])
            formatted_query = ndb.query.FilterNode(filtr["field"], filtr["operator"], filtr["value"])
            q = q.filter(formatted_query)

        return q


    def _formatSessionFilters(self, filters):
        """Parse, check validity and format user supplied filters."""
        formatted_filters = []
        inequality_field = None

        for f in filters:
            filtr = {field.name: getattr(f, field.name) for field in f.all_fields()}

            try:
                filtr["field"] = SESSION_FIELDS[filtr["field"]]
                filtr["operator"] = OPERATORS[filtr["operator"]]
            except KeyError:
                raise endpoints.BadRequestException("Filter contains invalid field or operator.")

            # Every operation except "=" is an inequality
            if filtr["operator"] != "=":
                # check if inequality operation has been used in previous filters
                # disallow the filter if inequality was performed on a different field before
                # track the field on which the inequality operation is performed
                if inequality_field and inequality_field != filtr["field"]:
                    raise endpoints.BadRequestException("Inequality filter is allowed on only one field.")
                else:
                    inequality_field = filtr["field"]

            formatted_filters.append(filtr)

        return (inequality_field, formatted_filters)


# - - - Wishlist methods - - - - - - - - - - - - - - - - - - -

    def _create_or_update_wishlist_object(self, request, session):
        """Add a session to a wishlist; create a wishlist if list doens't exist."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = _getUserId()

        # Get logged in user profile
        profile_key = ndb.Key(Profile, user_id)
        profile = self._getProfileFromUser()
        
        # Get current session key array and add new session to array;
        # return profile unchanged if session is already present
        session_keys = profile.wishlist_session_keys
        s_key = session.key.urlsafe()

        if s_key not in session_keys:
            session_keys.append(session.key.urlsafe())
            setattr(profile, 'wishlist_session_keys', session_keys)
            profile.put()
            
        return self._copyProfileToForm(profile)


    @endpoints.method(message_types.VoidMessage, SessionForms,
        path='wishlist',
        http_method='GET', name='getSessionsInWishlist')
    def getSessionsInWishList(self, request):
        """Return all sessions a user currently has on their wish list."""
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')
        user_id = _getUserId()

        # Get logged in user profile
        profile_key = ndb.Key(Profile, user_id)
        profile = self._getProfileFromUser()

        # Create a a list of tuples pairing each session with the profile of
        # the user who created it.
        session_keys = [ndb.Key(urlsafe=session) for session in profile.wishlist_session_keys]
        sessions = ndb.get_multi(session_keys)
        profile_keys = [ndb.Key(Profile, getattr(session, 'organizer_user_id')) for session in sessions]
        profiles = ndb.get_multi(profile_keys)

        paired_session_profile_tuples = zip(sessions, profiles)

        return SessionForms(
            items=[self._copySessionToForm(item[0], item[1].displayName) for item in paired_session_profile_tuples])


    @endpoints.method(WISHLIST_POST_REQUEST, ProfileForm,
        path='wishlist/{websafeSessionKey}',
        http_method='POST', name='addSessionToWishlist')
    def addSessionToWishList(self, request):
        """Add a conference session to a user's wishlist."""
        session = ndb.Key(urlsafe=request.websafeSessionKey).get()
        return self._create_or_update_wishlist_object(request, session)


# - - - Profile objects - - - - - - - - - - - - - - - - - - -

    def _copyProfileToForm(self, prof):
        """Copy relevant fields from Profile to ProfileForm."""
        # copy relevant fields from Profile to ProfileForm
        pf = ProfileForm()
        for field in pf.all_fields():
            if hasattr(prof, field.name):
                # convert t-shirt string to Enum; just copy others
                if field.name == 'teeShirtSize':
                    setattr(pf, field.name, getattr(TeeShirtSize, getattr(prof, field.name)))
                else:
                    setattr(pf, field.name, getattr(prof, field.name))
        pf.check_initialized()
        return pf


    def _getProfileFromUser(self):
        """Return user Profile from datastore, creating new one if non-existent."""
        # make sure user is authed
        user = endpoints.get_current_user()
        if not user:
            raise endpoints.UnauthorizedException('Authorization required')

        # get Profile from datastore
        user_id = _getUserId()
        p_key = ndb.Key(Profile, user_id)
        profile = p_key.get()
        # create new Profile if not there
        if not profile:
            profile = Profile(
                key = p_key,
                displayName = user.nickname(),
                mainEmail= user.email(),
                teeShirtSize = str(TeeShirtSize.NOT_SPECIFIED),
            )
            profile.put()

        return profile      # return Profile


    def _doProfile(self, save_request=None):
        """Get user Profile and return to user, possibly updating it first."""
        # get user Profile
        prof = self._getProfileFromUser()

        # if saveProfile(), process user-modifyable fields
        if save_request:
            for field in ('displayName', 'teeShirtSize'):
                if hasattr(save_request, field):
                    val = getattr(save_request, field)
                    if val:
                        setattr(prof, field, str(val))
                        #if field == 'teeShirtSize':
                        #    setattr(prof, field, str(val).upper())
                        #else:
                        #    setattr(prof, field, val)
                        prof.put()

        # return ProfileForm
        return self._copyProfileToForm(prof)


    @endpoints.method(message_types.VoidMessage, ProfileForm,
            path='profile', http_method='GET', name='getProfile')
    def getProfile(self, request):
        """Return user profile."""
        return self._doProfile()


    @endpoints.method(ProfileMiniForm, ProfileForm,
            path='profile', http_method='POST', name='saveProfile')
    def saveProfile(self, request):
        """Update & return user profile."""
        return self._doProfile(request)


# - - - Featured Speakers - - - - - - - - - - - - - - - - - - - -

    def _cacheFeaturedSpeakerInfo(self, session_data):
        """Keep a tally of potential featured speakers in memcache."""
        existing_speakers = []

        # the type differs depending on whether _cacheFeaturedSpeakerInfo
        # is being called via a task or cron job. 
        if isinstance(session_data['speakers'], list):
            speakers = session_data['speakers']
        else:
            speakers = ast.literal_eval(session_data['speakers'])
   
        # If a session has more than one speaker, try to add them to memcache,
        # increment number of sessions for that speaker otherwise
        if len(speakers) > 1:
            existing_speakers = memcache.add_multi(
                {"%s_%s" % (session_data['websafeConferenceKey'], speaker): 1 for speaker in speakers})
            if existing_speakers:
                [memcache.incr("%s_%s" % (session_data['websafeConferenceKey'], speaker)) for speaker in speakers]
            return

        # Do the same as above, except for single speakers
        conf_speaker_key = "%s_%s" % (
            session_data['websafeConferenceKey'], speakers[0])
        
        # Increment key if it exists, add it to memcache otherwise.
        incr = memcache.incr(conf_speaker_key)
        if incr is None:
            memcache.add(conf_speaker_key, value=1)
 

    def _cacheConferenceFeaturedSpeaker(self, websafeConferenceKey):
        """Determine a conference's featured speaker(s) and store them in
           memcache."""
        conference = ndb.Key(urlsafe=websafeConferenceKey).get()
        session_speakers = Session.query(ancestor=conference.key).\
            fetch(projection=[Session.speakers])
        featured_speakers = []

        speakers = [speaker.speakers[0] for speaker in session_speakers]
        cached_speakers = memcache.get_multi(speakers, key_prefix="%s_" % websafeConferenceKey)
        
        # If no results are returned, cache may need to be refreshed.
        if cached_speakers is {}:
            raise endpoints.BadRequestException(
                'No entries were found for featured speakers. There are either no speakers assigned to a conference or the cache needs to be refreshed.')

        # Find value for the highest number of sessions given by speaker
        if cached_speakers.values():
            session_num = max(cached_speakers.values())

            # Return all speakers matching session_num
            featured_speakers = [speaker for speaker, value in cached_speakers.iteritems() if value == session_num]
            conference.featured_speakers = featured_speakers
            conference.put()

        cfsf = ConferenceFeaturedSpeakerForm()
        setattr(cfsf, 'featured_speakers', featured_speakers or ['TBA'])
        setattr(cfsf, 'name', getattr(conference, 'name'))

        return cfsf

    @endpoints.method(FEATURED_SPEAKER_GET_REQUEST, ConferenceFeaturedSpeakerForm,
        path='conference/{websafeConferenceKey}/featuredspeaker', 
        http_method='GET', 
        name='getFeaturedSpeaker')
    def getFeaturedSpeaker(self, request):
        """Get the featured speaker for a given conference."""
        return self._cacheConferenceFeaturedSpeaker(request.websafeConferenceKey)


# - - - Announcements - - - - - - - - - - - - - - - - - - - -

    @staticmethod
    def _cacheAnnouncement():
        """Create Announcement & assign to memcache; used by
        memcache cron job & putAnnouncement().
        """
        confs = Conference.query(ndb.AND(
            Conference.seatsAvailable <= 5,
            Conference.seatsAvailable > 0)
        ).fetch(projection=[Conference.name])

        if confs:
            # If there are almost sold out conferences,
            # format announcement and set it in memcache
            announcement = '%s %s' % (
                'Last chance to attend! The following conferences '
                'are nearly sold out:',
                ', '.join(conf.name for conf in confs))
            memcache.set(MEMCACHE_ANNOUNCEMENTS_KEY, announcement)
        else:
            # If there are no sold out conferences,
            # delete the memcache announcements entry
            announcement = ""
            memcache.delete(MEMCACHE_ANNOUNCEMENTS_KEY)

        return announcement


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/get',
            http_method='GET', name='getAnnouncement')
    def getAnnouncement(self, request):
        """Return Announcement from memcache."""
        return StringMessage(data=memcache.get(MEMCACHE_ANNOUNCEMENTS_KEY) or "")


    @endpoints.method(message_types.VoidMessage, StringMessage,
            path='conference/announcement/put',
            http_method='GET', name='putAnnouncement')
    def putAnnouncement(self, request):
        """Put Announcement into memcache"""
        return StringMessage(data=self._cacheAnnouncement())


# - - - Registration - - - - - - - - - - - - - - - - - - - -

    @ndb.transactional(xg=True)
    def _conferenceRegistration(self, request, reg=True):
        """Register or unregister user for selected conference."""
        retval = None
        prof = self._getProfileFromUser() # get user Profile

        # check if conf exists given websafeConfKey
        # get conference; check that it exists
        wsck = request.websafeConferenceKey
        conf = ndb.Key(urlsafe=wsck).get()
        if not conf:
            raise endpoints.NotFoundException(
                'No conference found with key: %s' % wsck)

        # register
        if reg:
            # check if user already registered otherwise add
            if wsck in prof.conferenceKeysToAttend:
                raise ConflictException(
                    "You have already registered for this conference")

            # check if seats avail
            if conf.seatsAvailable <= 0:
                raise ConflictException(
                    "There are no seats available.")

            # register user, take away one seat
            prof.conferenceKeysToAttend.append(wsck)
            conf.seatsAvailable -= 1
            retval = True

        # unregister
        else:
            # check if user already registered
            if wsck in prof.conferenceKeysToAttend:

                # unregister user, add back one seat
                prof.conferenceKeysToAttend.remove(wsck)
                conf.seatsAvailable += 1
                retval = True
            else:
                retval = False

        # write things back to the datastore & return
        prof.put()
        conf.put()
        return BooleanMessage(data=retval)


    @endpoints.method(message_types.VoidMessage, ConferenceForms,
            path='conferences/attending',
            http_method='GET', name='getConferencesToAttend')
    def getConferencesToAttend(self, request):
        """Get list of conferences that user has registered for."""
        prof = self._getProfileFromUser() # get user Profile
        conf_keys = [ndb.Key(urlsafe=wsck) for wsck in prof.conferenceKeysToAttend]
        conferences = ndb.get_multi(conf_keys)

        # get organizers
        organisers = [ndb.Key(Profile, conf.organizerUserId) for conf in conferences]
        profiles = ndb.get_multi(organisers)

        # put display names in a dict for easier fetching
        names = {}
        for profile in profiles:
            names[profile.key.id()] = profile.displayName

        # return set of ConferenceForm objects per Conference
        return ConferenceForms(items=[self._copyConferenceToForm(conf, names[conf.organizerUserId])\
         for conf in conferences]
        )


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='POST', name='registerForConference')
    def registerForConference(self, request):
        """Register user for selected conference."""
        return self._conferenceRegistration(request)


    @endpoints.method(CONF_GET_REQUEST, BooleanMessage,
            path='conference/{websafeConferenceKey}',
            http_method='DELETE', name='unregisterFromConference')
    def unregisterFromConference(self, request):
        """Unregister user for selected conference."""
        return self._conferenceRegistration(request, reg=False)


api = endpoints.api_server([ConferenceApi]) # register API
