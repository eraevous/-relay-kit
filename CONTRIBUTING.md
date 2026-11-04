# Contributing

Thanks for considering a contribution.

1. Fork the repository and create a branch.
2. Make your change, with tests (`python3 -I -B tests/run_tests.py` and
   `node tests/browser_tests.cjs`). Changing a format? Read the contract rule in
   [ARCHITECTURE.md](ARCHITECTURE.md): new version, every implementer in the same change.
3. Open a pull request and check the CLA box in the template.
4. **Review.** A change to a contract, to a check or guard, or to anything that decides who
   may do what needs an outside review before merging: at least two reviewers that didn't
   build it, one from a different model family than the builder (for example Copilot's
   pull-request review and a ChatGPT assistant in a Temporary Chat),
   using `handoff.py new --review`. Fix or answer every finding on the pull request, with a
   test that fails when the fix is removed.

## Releases

The version is in `pyproject.toml` and `tools/__init__.py`. A release is a commit on main
tagged `vX.Y.Z` by the owner. Anything that depends on the kit (Telosmith, a records
repository's CI made by `relay-kit init`) pins a tag, or better a commit, never a branch.
A change to a contract's format is at least a minor version; a change to `relay-kit init`'s
template only affects records made after it.

## License

The project is licensed under the terms in [LICENSE](LICENSE). Contributions are made
under the [Contributor License Agreement](CLA.md): they're licensed like the project, you
keep your copyright, and the maintainer may also offer them in commercial versions.
