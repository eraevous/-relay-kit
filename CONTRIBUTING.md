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

## License

The project is licensed under the terms in [LICENSE](LICENSE). Contributions are made
under the [Contributor License Agreement](CLA.md): they're licensed like the project, you
keep your copyright, and the maintainer may also offer them in commercial versions.
