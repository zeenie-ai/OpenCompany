# Temporary checkout return page

This branch contains a standalone return page for testing the OpenCompany / Zeenie AI Dodo Payments checkout. GitHub Pages serves this branch's root directory.

The page displays the status supplied in the checkout URL. It does not independently verify payments, fulfill purchases, collect payment details, or confirm bank settlement. Verify payment results in Dodo Payments or through a trusted server webhook.

Customer email parameters are ignored, and query parameters are removed from the address bar after rendering. No analytics or third-party assets are used.

Retire this temporary page by disabling GitHub Pages after moving the checkout return URL to the application.