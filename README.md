A conference scheduling app and API built on Google App Engine.

## Products
- [App Engine][1]

## Language
- [Python][2]

## APIs
- [Google Cloud Endpoints][3]

## Setup Instructions
1. Update the value of `application` in `app.yaml` to the app ID you
   have registered in the App Engine admin console and would like to use to host
   your instance of this sample.
1. Update the values at the top of `settings.py` to
   reflect the respective client IDs you have registered in the
   [Developer Console][4].
1. Update the value of CLIENT_ID in `static/js/app.js` to the Web client ID
1. (Optional) Mark the configuration files as unchanged as follows:
   `$ git update-index --assume-unchanged app.yaml settings.py static/js/app.js`
1. Run the app with the devserver using `dev_appserver.py DIR`, and ensure it's running by visiting your local server's address (by default [localhost:8080][5].)
1. (Optional) Generate your client library(ies) with [the endpoints tool][6].
1. Deploy your application.


## Design Choices

#### Sessions

New model classes: `Session`, `SessionForm`, `SessionForms`

New endpoints/methods: `_copySessionToForm`, `_createSessionObject`, `getConferenceSessions`, `getConferenceSessionsByType`, `getSessionsBySpeaker`, `createSession`

New tasks/cron: `SendSessionConfirmationEmailHandler`

1. Sessions are descendent objects for an individual Conference class ie. a Conference can have many sessions, but a session can only belong to a single conference.
2. Sessions can only be edited by the conference organizer. This is intentional in order to give the conference owner greater quality control over outward-facing conference content.
3. Since sometimes a session may be something like a panel discussion or fireside chat, a session is permitted to have one or more speakers. Speakers are implemented as a StringProperty repeated to account for this.
4. A new task was added to send an email to the organizer when a new session is added.
5. A note on Session property types:
  * IntegerProperty is being used for `duration`, but it would probably be better to change this to a TimeProperty and rename to `end_date`.
  * Date is used for `date` since hour, minute, etc data isn't needed. Dates must be entered as MM/DD/YYYY.
  * TimeProperty is used for `start_time` since calendar date isn't needed. Times must be entered as HH:MM. 

Next steps:

1. Normalize parameters sent to `getConferenceSessionsByType` and `getSessionsBySpeaker`. Currently entering something like 'Python' will not match a session named 'python' which is obviously suboptimal.
2. Create endpoint to allow an organizer to edit/update sessions.

#### Speakers

I decided not to implement speakers as a separate entity at this time. This was done to simplify the data schema and keep the indexes clean. It's also not clear to me that a separate entity is even needed considering we already have a Profile class that is essentially what we're looking for.

If I did implement Speakers as a separate entity, this is how I would probably do it:

1. Extend the Profile class by adding additional fields needed to store potential sessions spoken at. A new field for email will likely need to added and enforced as unique.
2. Move away from storing names for Speakers and Featured Speakers and start storing Profile keys. This would allow speakers to be easily queried.
3. Since only conference organizers can add/update/delete sessions and a potential speaker may or may not have registered a user profile yet, validation measures will be needed.
    1. Organizers add speakers to a session by email. They can add and remove speakers via email after the session is created.
    2. Email is used to get the speaker's Profile object. If it doesn't exist, create one.
    3. When a speaker logs in via Google sign-in for the first time, their email is compared against emails in the datastore. If they have been added as a speaker previously and are logging in for the first time, they should be logged into that Profile.
    4. If they register with an email different from the one the organizer used, a new Profile will be created, but they will not be able to access the sessions they speak at.
    5. A speaker cannot edit or delete a session, even if they are speaking at it (unless they happen to also be the organizer). Only organizers have permission to do this.


#### Wishlists

New classes: `ProfileWishListForm`

New endpoints/methods: `_create_or_update_wishlist_object`, `getSessionsInWishList`, `addSessionToWishList`

New tasks/cron: None

1.  Rather than create a new model class, the Profile class was modified to accept a list of session keys under a new Profile field. This was done for a few reasons:
   1. The API supports multiple conferences from multiple possible users. It just makes sense to create a model where a user in this system can attend multiple conferences, attend multiple sessions, but have all this information stored under a single user profile rather than keep track of several Wishlist instances for each conference.
   2. Storing only Session keys rather than entire Session objects reduces Profile model bloat.
   3. `PUT` is used in lieu of `DELETE` for removeSessionsInWishlist since we are updating sessions in the wishlist, rather than delete the session altogether.
2. All Sessions are given a unique id to preserve uniqueness across multiple conferences.


#### Additional Queries

New model classes: `SessionQueryForm`, `SessionQueryForms`

New endpoints/methods: `_getSessionQuery`, `_formatSessionsFilters`, `querySessions`, `querySessionsSpecial`

New tasks/cron: None

1. The "Problem Query". Since session types are not equal to workshops and start times after 7 pm are both inequalities, I couldn't just chain filters together until I got a result. I couldn't quickly arrive at an efficient solution so my current solution is far less elegant that it should be and unfortunately quite hard-coded. 
Having an "AND" query made this is a bit easier. I chose sessions after 7 pm as the Datastore inequality query since in my experience, very few conferences feature sessions after that time (and thus fewer results and fewer system resources needed). Then it was just a matter of iterating through the results and adding records that didn't equal "Workshop" and returning a new list.
2. This is just really bad as it stands. The name of the endpoint sucks and having such a specific query hard-coded is pretty much useless. I will need to dive into the two private helper methods to divy the query up into something much more abstract and useful.

Next steps: 

1. Create one canonical Session endpoint and allow Session fields to be passed in as query parameters.
2. Underneath the hood, split invalid queries containing inequalities on multiple properties into multiple valid queries. Use the results of each query to filter out records until only the records existing in all valid queries are left.
3. Normalize textual queries to match differences in case.

#### Featured Speakers and additionial Tasks

New model classes: `ConferenceFeaturedSpeakerForm`

New endpoints/methods: `getFeaturedSpeaker`, `_cacheConferenceFeaturedSpeaker`

New tasks/cron: `SetFeaturedSpeakerHandler`
 
1. The `getFeaturedSpeaker` endpoint takes a conference key as a parameter and returns the current featured speaker(s) for said conference. The information needed is retrieved solely from memcache.
1. Just as a session can have multiple speakers, a conference can have multiple featured speakers. A speaker can be a featured speaker if they have, or are tied for, the most sessions spoken at within a specific conference.
2. A new task was added to handle calculating and storing featured speaker info in memcache. This task is invoked in the `_createSessionObject` to update featured speakers everytime a new session is added.
3. A new cron job was also added to periodically update the featured speaker cache. This was done since no functionality exists in the app currently to update the cache when conferences or sessions are updated/deleted. The job flushes the cache, grabs all Session objects, and calls `_cacheFeaturedSpeakerInfo` to replicate the process laid out in #2.
4. `get_multi` and projection queries were used in the fetching and storing of featured speaker data as query optimizations.
5. Note: featured speakers are stored in memcache with a key of `websafeConferenceKey`.


Next steps:

1. Allow an organizer to manually set the featured speaker. This would require adding a new field to the Conference model and updating the `updateConference` endpoint.
2. Alter update/delete methods to updated featured speaker cache on change. 


[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool
