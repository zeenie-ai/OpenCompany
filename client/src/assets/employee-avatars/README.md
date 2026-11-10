# Employee portraits

These six 256 x 256 PNGs are exact copies of the approved exports in
`assets/employees/avatars/avatars-256/`.
That source pack retains the masters, generation prompts, and provenance.

The application imports these files through Vite so they are included in
production builds with asset hashes. `features/home/data/avatars.ts` selects
a stable random-looking default using the employee's permanent workflow ID.
Portrait filenames identify the original artwork; they do not assign a job
role to the employee using that image. Custom employee photos take priority.
