#!/usr/bin/env python

"""
main.py -- Udacity conference server-side Python App Engine
    HTTP controller handlers for memcache & task queue access

$Id$

created by wesc on 2014 may 24

"""

__author__ = 'wesc+api@google.com (Wesley Chun)'

import webapp2
from google.appengine.api import app_identity
from google.appengine.api import mail
from google.appengine.api import memcache

from conference import ConferenceApi
from models import Session


class SetAnnouncementHandler(webapp2.RequestHandler):
    def get(self):
        """Set Announcement in Memcache."""
        ConferenceApi._cacheAnnouncement()
        self.response.set_status(204)


class SendConferenceConfirmationEmailHandler(webapp2.RequestHandler):
    def post(self):
        """Send email confirming Conference creation."""
        mail.send_mail(
            'noreply@%s.appspotmail.com' % (
                app_identity.get_application_id()),     # from
            self.request.get('email'),                  # to
            'You created a new Conference!',            # subj
            'Hi, you have created a following '         # body
            'conference:\r\n\r\n%s' % self.request.get(
                'conferenceInfo')
        )


class SendSessionConfirmationEmailHandler(webapp2.RequestHandler):
    def post(self):
        """Send email confirming Session creation."""
        mail.send_mail(
            'noreply@%s.appspotmail.com' % (
                app_identity.get_application_id()),     # from
            self.request.get('email'),                  # to
            'You created a new Session!',            # subj
            'Hi, you have created the following '         # body
            'session\r\n\r\n%s' % self.request.get(
                'sessionInfo')
        )


class SetFeaturedSpeakerHandler(webapp2.RequestHandler):
    def post(self):
        """Set the featured speaker(s) for a conference."""
        conf_api = ConferenceApi()
        conf_api._cacheConferenceFeaturedSpeaker(
            self.request.get('websafeConferenceKey')
        )
        self.response.set_status(204)        


class RefreshFeaturedSpeakerCacheHandler(webapp2.RequestHandler):
    def get(self):
        """Periodically refresh featured speaker info in memcache."""
        
        # Start with a fresh cache
        memcache.flush_all()

        # Iterate through all sessions, updating featured speaker cache
        conf_api = ConferenceApi()        
        conf_api._cacheConferenceFeaturedSpeaker(
            self.request.get('websafeConferenceKey')
        )
        self.response.set_status(204) 


app = webapp2.WSGIApplication([
    ('/crons/set_announcement', SetAnnouncementHandler),
    ('/crons/refresh_featured_speaker_cache', RefreshFeaturedSpeakerCacheHandler),
    ('/tasks/send_conference_confirmation_email', SendConferenceConfirmationEmailHandler),
    ('/tasks/send_session_confirmation_email', SendSessionConfirmationEmailHandler),
    ('/tasks/set_featured_speaker', SetFeaturedSpeakerHandler)
], debug=True)
