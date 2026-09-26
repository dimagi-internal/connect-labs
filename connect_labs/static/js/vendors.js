import './htmx';
// The htmx 2 build of the extension, not htmx.org/dist/ext/loading-states.
// htmx 2 still ships that path, but the file there is the htmx 1 extension and
// it warns at runtime on every page: "You are using an htmx 1 extension with
// htmx 2.0.10". Resolving is not the same as being the right build.
import 'htmx-ext-loading-states';

import './alpine';
import './flatpickr';

import 'open-chat-studio-widget';
