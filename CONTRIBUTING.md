# Contributing

Issues and pull requests are welcome.

- Fork the repo, branch off `main`, and open a pull request.
- `main` is protected: changes land only through a pull request reviewed and
  merged by a maintainer. Direct pushes are limited to maintainers.
- Read `docs/architecture/v2.md` first: module layout, the shared state contract and
  the rules. The design reference is `docs/design/prototype.html`.
- Keep sensors and services non-raising, and every GLib main-loop callback under
  50 ms (`smartpanel.timing` logs any that isn't); slow work goes on a thread.
- Add a test for any pure logic you touch: `pytest`. For UI changes, look at the
  result: `python3 -m smartpanel --demo busy --snapshot <page> --out shot.png`
  (light and dark), or `./scripts/screenshots.sh` for the whole set.

By contributing you agree that your contribution is licensed under the MIT
License of this project.
