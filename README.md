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

1. Sessions are descendent objects for an individual Conference class ie. a Conference can have many sessions, but a session can only belong to a single conference.

2. Sessions can only be edited by the conference organizer. This is intentional in order to give the conference owner greater quality control over outward-facing conference content.

3. Since sometimes a session may be something like a panel discussion or fireside chat, a session is permitted to have one or more speakers.

Next steps:

1. Normalize parameters sent to `getConferenceSessionsByType` and `getSessionsBySpeaker`. Currently entering something like 'Python' will not match a session named 'python' which is obviously suboptimal.
2. Create endpoint to allow an organizer to edit/update sessions.

#### Wishlists

New model classes: `ProfileWishListForm`
New endpoints/methods: `_create_or_update_wishlist_object`, `getSessionsInWishList`, `addSessionToWishList`

1.  Rather than create a new model class, the Profile class was modified to accept a list of session keys under a new Profile field. This was done for a few reasons:
   1. The API supports multiple conferences from multiple possible users. It just makes sense to create a model where a user in this system can attend multiple conferences, attend multiple sessions, but have all this information stored under a single user profile rather than keep track of several Wishlist instances for each conference.
   1. Less computational overhead. Having a potentially many Wishlist entities per Profile, each containing potentially multiple Sessions, one would likely be looking at quadratic time lookups. This at least ensures the possibility of linear time lookups.
   2. Storing only Session keys rather than entire Session objects reduces Profile model bloat.
2. All Sessions are given a unique id to preserve uniqueness across multiple conferences.


#### Additional Queries

New model classes: `SessionQueryForm`, `SessionQueryForms`
New endpoints/methods: `_getSessionQuery`, `_formatSessionsFilters`, `querySessions`, `querySessionsSpecial`

1. The "Problem Query". Since session types not equal to workshops and start times after 7 pm are both inequalities, I couldn't just chain filters together until I got a result. I couldn't quickly arrive at an efficient solution so my current solution is far less elegant that it should be and unfortunately quite hard-coded. 
Having an "AND" query made this is a bit easier. I chose sessions after 7 pm as the Datastore inequality query since in my experience, very few conferences feature sessions after that time (and thus fewer results and fewer system resources needed). Then it was just a matter of interating through the results and adding records that didn't equal "Workshop" into a new array and returning it.
2. This is just really bad as it stands. The name of the endpoint sucks and having such a specific query hard-coded is pretty much useless. I will need to dive into the two private helper methods to divy the query up into something much more abstract and useful.

Next steps: 

1. Create one canonical Session endpoint and allow Session fields to be passed in as query parameters.
2. Underneath the hood, split invalid queries containing inequalities on multiple properties into multiple valid queries. Use the results of each query to filter out records until only the records existing in all valid queries are left.
3. Normalize textual queries to match differences in case.

#### Additionial Tasks



### Links

[1]: https://developers.google.com/appengine
[2]: http://python.org
[3]: https://developers.google.com/appengine/docs/python/endpoints/
[4]: https://console.developers.google.com/
[5]: https://localhost:8080/
[6]: https://developers.google.com/appengine/docs/python/endpoints/endpoints_tool
