## Trusted Libraries Support Matrices

### Legend

| Emoji | Meaning |
| --- | --- |
| :x: | No Support |
| :soon: | Support is planned |
| :white_check_mark: | Currently Supported | 

### Manylinux

We target `manylinux_2_28` compatibility.\
Some wheels may be upgraded and support even older versions.\
For example, `numpy-2.4.2-0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64`\
This wheel has `manylinux_2_27` and `manylinux_2_28` compatibility.

### Python Versions

| 2.X | 3.9 | 3.10 | 3.11 | 3.12 | 3.13 | 3.14 |
| --- | --- | ---- | ---- | ---- | ---- | ---- | 
| :x: | :x: | :x: | :soon: |  :white_check_mark: | :soon:| :soon: |

Note: Universal wheels may have python2.X compatibility but we do not guarantee functionality.

[Python3.12.12 download](https://www.python.org/downloads/release/python-31212/)


### Architectures

| x86_64 | aarch64 |
| --- | --- |
| :white_check_mark: | :soon: |


### Integration Tests

We run [integration tests](https://github.com/calungaproject/plumbing/blob/main/tasks/install-and-import-wheels.yaml)
against the following images / operating systems:

| ubi8 | ubi9 | ubi10 | fedora43 | hummingbird/python3.12 | ubuntu24.04 |
| --- | --- | --- | --- | --- | --- |
| :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: | :white_check_mark: |