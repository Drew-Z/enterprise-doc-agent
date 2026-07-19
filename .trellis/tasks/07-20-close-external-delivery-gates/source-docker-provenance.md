Gordon, your AI assistant for Docker docs

Start a new chat

### What can I help you with?

I'm Gordon, your AI assistant for Docker and documentation questions.

Try asking

Was this helpful?

remaining in this thread.

You've reached the maximum of questions per thread. For better answer quality, start a new thread.

Answers are generated based on the documentation.

* [Get started](/get-started/)
* [Guides](/guides/)
* [Reference](/reference/)

# Provenance attestations

---

Table of contents

---

The provenance attestations include facts about the build process, including details such as:

* Build timestamps
* Build parameters and environment
* Version control metadata
* Source code details
* Materials (files, scripts) consumed during the build

By default, provenance attestations follow the [SLSA provenance schema, version 0.2](https://slsa.dev/spec/v0.2/provenance#schema). You can optionally enable [SLSA Provenance v1](https://slsa.dev/spec/v1.1/provenance#schema) using [the `version` parameter](#version).

For more information about how BuildKit populates these provenance properties, refer to [SLSA definitions](https://docs.docker.com/build/metadata/attestations/slsa-definitions/).

## [Create provenance attestations](#create-provenance-attestations)

To create a provenance attestation, pass the `--attest type=provenance` option to the `docker buildx build` command:

```
$ docker buildx build --tag /: \ $ docker buildx build --tag /: \ $ \  --attest type=provenance,mode=[min,max],version=[v0.2,v1] .  --attest type=provenance,mode=[min,max],version=[v0.2,v1] .  --attest type=provenance,mode=[min,max],version=[v0.2,v1] . 
```

Alternatively, you can use the shorthand `--provenance=true` option instead of `--attest type=provenance`. To specify the `mode` or `version` parameters using the shorthand option, use: `--provenance=mode=max,version=v1`.

For an example on how to add provenance attestations with GitHub Actions, see [Add attestations with GitHub Actions](https://docs.docker.com/build/ci/github-actions/attestations/).

## [Mode](#mode)

You can use the `mode` parameter to define the level of detail to be included in the provenance attestation. Supported values are `mode=min` (default) and `mode=max`.

### [Min](#min)

In `min` mode, the provenance attestations include a minimal set of information, such as:

* Build timestamps
* The frontend used
* Build materials
* Source repository and revision
* Build platform
* Reproducibility

Values of build arguments, the identities of secrets, and rich layer metadata are not included in `mode=min`. The `min`-level provenance is safe to use for all builds, as it doesn't leak information from any part of the build environment.

The following JSON example shows the information included in a provenance attestations created using the `min` mode:

```
{ { { "_type": "https://in-toto.io/Statement/v0.1",  "_type": "https://in-toto.io/Statement/v0.1", "_type":"https://in-toto.io/Statement/v0.1", "predicateType": "https://slsa.dev/provenance/v0.2",  "predicateType": "https://slsa.dev/provenance/v0.2", "predicateType":"https://slsa.dev/provenance/v0.2", "subject": [  "subject": [ "subject":[ {  { { "name": "pkg:docker//@?platform=",  "name": "pkg:docker//@?platform=", "name":"pkg:docker//@?platform=", "digest": {  "digest": { "digest":{ "sha256": "e8275b2b76280af67e26f068e5d585eb905f8dfd2f1918b3229db98133cb4862"  "sha256": "e8275b2b76280af67e26f068e5d585eb905f8dfd2f1918b3229db98133cb4862" "sha256": "e8275b2b76280af67e26f068e5d585eb905f8dfd2f1918b3229db98133cb4862" }  } } }  } } ],  ], ], "predicate": {  "predicate": { "predicate":{ "builder": { "id": "" },  "builder": { "id": "" }, "builder":{"id": ""}, "buildType": "https://mobyproject.org/buildkit@v1",  "buildType": "https://mobyproject.org/buildkit@v1", "buildType":"https://mobyproject.org/buildkit@v1", "materials": [  "materials": [ "materials":[ {  { { "uri": "pkg:docker/docker/dockerfile@1",  "uri": "pkg:docker/docker/dockerfile@1", "uri":"pkg:docker/docker/dockerfile@1", "digest": {  "digest": { "digest":{ "sha256": "9ba7531bd80fb0a858632727cf7a112fbfd19b17e94c4e84ced81e24ef1a0dbc"  "sha256": "9ba7531bd80fb0a858632727cf7a112fbfd19b17e94c4e84ced81e24ef1a0dbc" "sha256": "9ba7531bd80fb0a858632727cf7a112fbfd19b17e94c4e84ced81e24ef1a0dbc" }  } } },  }, }, {  { { "uri": "pkg:docker/golang@1.19.4-alpine?platform=linux%2Farm64",  "uri": "pkg:docker/golang@1.19.4-alpine?platform=linux%2Farm64", "uri":"pkg:docker/golang@1.19.4-alpine?platform=linux%2Farm64", "digest": {  "digest": { "digest":{ "sha256": "a9b24b67dc83b3383d22a14941c2b2b2ca6a103d805cac6820fd1355943beaf1"  "sha256": "a9b24b67dc83b3383d22a14941c2b2b2ca6a103d805cac6820fd1355943beaf1" "sha256": "a9b24b67dc83b3383d22a14941c2b2b2ca6a103d805cac6820fd1355943beaf1" }  } } }  } } ],  ], ], "invocation": {  "invocation": { "invocation":{ "configSource": { "entryPoint": "Dockerfile" },  "configSource": { "entryPoint": "Dockerfile" }, "configSource":{"entryPoint": "Dockerfile"}, "parameters": {  "parameters": { "parameters":{ "frontend": "gateway.v0",  "frontend": "gateway.v0", "frontend":"gateway.v0", "args": {  "args": { "args":{ "cmdline": "docker/dockerfile:1",  "cmdline": "docker/dockerfile:1", "cmdline":"docker/dockerfile:1", "source": "docker/dockerfile:1",  "source": "docker/dockerfile:1", "source":"docker/dockerfile:1", "target": "binaries"  "target": "binaries" "target": "binaries" },  }, }, "locals": [{ "name": "context" }, { "name": "dockerfile" }]  "locals": [{ "name": "context" }, { "name": "dockerfile" }] "locals":[{"name": "context"},{"name": "dockerfile"}] },  }, }, "environment": { "platform": "linux/arm64" }  "environment": { "platform": "linux/arm64" } "environment":{"platform":"linux/arm64"} },  }, }, "metadata": {  "metadata": { "metadata":{ "buildInvocationID": "c4a87v0sxhliuewig10gnsb6v",  "buildInvocationID": "c4a87v0sxhliuewig10gnsb6v", "buildInvocationID": "c4a87v0sxhliuewig10gnsb6v", "buildStartedOn": "2022-12-16T08:26:28.651359794Z",  "buildStartedOn": "2022-12-16T08:26:28.651359794Z", "buildStartedOn":"2022-12-16T08:26:28.651359794Z", "buildFinishedOn": "2022-12-16T08:26:29.625483253Z",  "buildFinishedOn": "2022-12-16T08:26:29.625483253Z", "buildFinishedOn":"2022-12-16T08:26:29.625483253Z", "reproducible": false,  "reproducible": false, "reproducible": false, "completeness": {  "completeness": { "completeness":{ "parameters": true,  "parameters": true, "parameters": true, "environment": true,  "environment": true, "environment": true, "materials": false  "materials": false "materials": false },  }, }, "https://mobyproject.org/buildkit@v1#metadata": {  "https://mobyproject.org/buildkit@v1#metadata": { "https://mobyproject.org/buildkit@v1#metadata":{ "vcs": {  "vcs": { "vcs":{ "revision": "a9ba846486420e07d30db1107411ac3697ecab68",  "revision": "a9ba846486420e07d30db1107411ac3697ecab68", "revision": "a9ba846486420e07d30db1107411ac3697ecab68", "source": "git@github.com:/.git"  "source": "git@github.com:/.git" "source":"git@github.com:/.git" }  } } }  } } }  } } }  } }}}}
```

### [Max](#max)

The `max` mode includes all of the information included in the `min` mode, as well as:

* The LLB definition of the build. These show the exact steps taken to produce the image.
* Information about the Dockerfile, including a full base64-encoded version of the file.
* Source maps describing the relationship between build steps and image layers.

When possible, you should prefer `mode=max` as it contains significantly more detailed information for analysis.

> Note that `mode=max` exposes the values of [build arguments](/reference/cli/docker/buildx/build/#build-arg).
>
> If you're misusing build arguments to pass credentials, authentication tokens, or other secrets, you should refactor your build to pass the secrets using [secret mounts](/reference/cli/docker/buildx/build/#secret) instead. Secret mounts don't leak outside of the build and are never included in provenance attestations.

## [Version](#version)

The `version` parameter lets you specify which SLSA provenance schema version to use. Supported values are `version=v0.2` (default) and `version=v1`.

To use SLSA Provenance v1:

```
$ docker buildx build --tag /: \ $ docker buildx build --tag /: \ $ \  --attest type=provenance,mode=max,version=v1 .  --attest type=provenance,mode=max,version=v1 .  --attest type=provenance,mode=max,version=v1 . 
```

For more information about SLSA Provenance v1, see the [SLSA specification](https://slsa.dev/spec/v1.1/provenance). To see the difference between SLSA v0.2 and v1 provenance attestations, refer to [SLSA definitions](https://docs.docker.com/build/metadata/attestations/slsa-definitions/)

## [Inspecting Provenance](#inspecting-provenance)

To explore created Provenance exported through the `image` exporter, you can use [`imagetools inspect`](/reference/cli/docker/buildx/imagetools/inspect/).

Using the `--format` option, you can specify a template for the output. All provenance-related data is available under the `.Provenance` attribute. For example, to get the raw contents of the Provenance in the SLSA format:

```
$ docker buildx imagetools inspect /: \ $ docker buildx imagetools inspect /: \ $ \  --format "{{ json .Provenance.SLSA }}"  --format "{{ json .Provenance.SLSA }}"  --format "{{ json .Provenance.SLSA }}" { { {  "buildType": "https://mobyproject.org/buildkit@v1",  "buildType": "https://mobyproject.org/buildkit@v1",  "buildType": "https://mobyproject.org/buildkit@v1",  ...  ...  ... } } } 
```

You can also construct more complex expressions using the full functionality of Go templates. For example, for provenance generated with `mode=max`, you can extract the full source code of the Dockerfile used to build the image:

```
$ docker buildx imagetools inspect /: \ $ docker buildx imagetools inspect /: \ $ \  --format '{{ range (index .Provenance.SLSA.metadata "https://mobyproject.org/buildkit@v1#metadata").source.infos }}{{ if eq .filename "Dockerfile" }}{{ .data }}{{ end }}{{ end }}' | base64 -d  --format '{{ range (index .Provenance.SLSA.metadata "https://mobyproject.org/buildkit@v1#metadata").source.infos }}{{ if eq .filename "Dockerfile" }}{{ .data }}{{ end }}{{ end }}' | base64 -d  --format '{{ range (index .Provenance.SLSA.metadata "https://mobyproject.org/buildkit@v1#metadata").source.infos }}{{ if eq .filename "Dockerfile" }}{{ .data }}{{ end }}{{ end }}' | base64 -d FROM ubuntu:24.04 FROM ubuntu:24.04 FROM ubuntu:24.04 RUN apt-get update RUN apt-get update RUN apt-get update ... ... ... 
```

## [Provenance attestation example](#provenance-attestation-example)

The following example shows what a JSON representation of a provenance attestation with `mode=max` looks like:

```
{ { { "_type": "https://in-toto.io/Statement/v0.1",  "_type": "https://in-toto.io/Statement/v0.1", "_type":"https://in-toto.io/Statement/v0.1", "predicateType": "https://slsa.dev/provenance/v0.2",  "predicateType": "https://slsa.dev/provenance/v0.2", "predicateType":"https://slsa.dev/provenance/v0.2", "subject": [  "subject": [ "subject":[ {  { { "name": "pkg:docker//@?platform=",  "name": "pkg:docker//@?platform=", "name":"pkg:docker//@?platform=", "digest": {  "digest": { "digest":{ "sha256": "e8275b2b76280af67e26f068e5d585eb905f8dfd2f1918b3229db98133cb4862"  "sha256": "e8275b2b76280af67e26f068e5d585eb905f8dfd2f1918b3229db98133cb4862" "sha256": "e8275b2b76280af67e26f068e5d585eb905f8dfd2f1918b3229db98133cb4862" }  } } }  } } ],  ], ], "predicate": {  "predicate": { "predicate":{ "builder": { "id": "" },  "builder": { "id": "" }, "builder":{"id": ""}, "buildType": "https://mobyproject.org/buildkit@v1",  "buildType": "https://mobyproject.org/buildkit@v1", "buildType":"https://mobyproject.org/buildkit@v1", "materials": [  "materials": [ "materials":[ {  { { "uri": "pkg:docker/docker/dockerfile@1",  "uri": "pkg:docker/docker/dockerfile@1", "uri":"pkg:docker/docker/dockerfile@1", "digest": {  "digest": { "digest":{ "sha256": "9ba7531bd80fb0a858632727cf7a112fbfd19b17e94c4e84ced81e24ef1a0dbc"  "sha256": "9ba7531bd80fb0a858632727cf7a112fbfd19b17e94c4e84ced81e24ef1a0dbc" "sha256": "9ba7531bd80fb0a858632727cf7a112fbfd19b17e94c4e84ced81e24ef1a0dbc" }  } } },  }, }, {  { { "uri": "pkg:docker/golang@1.19.4-alpine?platform=linux%2Farm64",  "uri": "pkg:docker/golang@1.19.4-alpine?platform=linux%2Farm64", "uri":"pkg:docker/golang@1.19.4-alpine?platform=linux%2Farm64", "digest": {  "digest": { "digest":{ "sha256": "a9b24b67dc83b3383d22a14941c2b2b2ca6a103d805cac6820fd1355943beaf1"  "sha256": "a9b24b67dc83b3383d22a14941c2b2b2ca6a103d805cac6820fd1355943beaf1" "sha256": "a9b24b67dc83b3383d22a14941c2b2b2ca6a103d805cac6820fd1355943beaf1" }  } } }  } } ],  ], ], "buildConfig": {  "buildConfig": { "buildConfig":{ "llbDefinition": [  "llbDefinition": [ "llbDefinition":[ {  { { "id": "step4",  "id": "step4", "id": "step4", "op": {  "op": { "op":{ "Op": {  "Op": { "Op":{ "exec": {  "exec": { "exec":{ "meta": {  "meta": { "meta":{ "args": ["/bin/sh", "-c", "go mod download -x"],  "args": ["/bin/sh", "-c", "go mod download -x"], "args":["/bin/sh","-c","go mod download -x"], "env": [  "env": [ "env":[ "PATH=/go/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",  "PATH=/go/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "PATH=/go/bin:/usr/local/go/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "GOLANG_VERSION=1.19.4",  "GOLANG_VERSION=1.19.4", "GOLANG_VERSION=1.19.4", "GOPATH=/go",  "GOPATH=/go", "GOPATH=/go", "CGO_ENABLED=0"  "CGO_ENABLED=0" "CGO_ENABLED=0" ],  ], ], "cwd": "/src"  "cwd": "/src" "cwd":"/src" },  }, }, "mounts": [  "mounts": [ "mounts":[ { "input": 0, "dest": "/", "output": 0 },  { "input": 0, "dest": "/", "output": 0 }, {"input": 0, "dest":"/", "output": 0}, {  { { "input": -1,  "input": -1, "input": -1, "dest": "/go/pkg/mod",  "dest": "/go/pkg/mod", "dest":"/go/pkg/mod", "output": -1,  "output": -1, "output": -1, "mountType": 3,  "mountType": 3, "mountType": 3, "cacheOpt": { "ID": "//go/pkg/mod" }  "cacheOpt": { "ID": "//go/pkg/mod" } "cacheOpt":{"ID":"//go/pkg/mod"} },  }, }, {  { { "input": 1,  "input": 1, "input": 1, "selector": "/go.mod",  "selector": "/go.mod", "selector":"/go.mod", "dest": "/src/go.mod",  "dest": "/src/go.mod", "dest":"/src/go.mod", "output": -1,  "output": -1, "output": -1, "readonly": true  "readonly": true "readonly": true },  }, }, {  { { "input": 1,  "input": 1, "input": 1, "selector": "/go.sum",  "selector": "/go.sum", "selector":"/go.sum", "dest": "/src/go.sum",  "dest": "/src/go.sum", "dest":"/src/go.sum", "output": -1,  "output": -1, "output": -1, "readonly": true  "readonly": true "readonly": true }  } } ]  ] ] }  } } },  }, }, "platform": { "Architecture": "arm64", "OS": "linux" },  "platform": { "Architecture": "arm64", "OS": "linux" }, "platform":{"Architecture": "arm64", "OS": "linux"}, "constraints": {}  "constraints": {} "constraints":{} },  }, }, "inputs": ["step3:0", "step1:0"]  "inputs": ["step3:0", "step1:0"] "inputs":["step3:0","step1:0"] }  } } ]  ] ] },  }, }, "metadata": {  "metadata": { "metadata":{ "buildInvocationID": "edf52vxjyf9b6o5qd7vgx0gru",  "buildInvocationID": "edf52vxjyf9b6o5qd7vgx0gru", "buildInvocationID": "edf52vxjyf9b6o5qd7vgx0gru", "buildStartedOn": "2022-12-15T15:38:13.391980297Z",  "buildStartedOn": "2022-12-15T15:38:13.391980297Z", "buildStartedOn":"2022-12-15T15:38:13.391980297Z", "buildFinishedOn": "2022-12-15T15:38:14.274565297Z",  "buildFinishedOn": "2022-12-15T15:38:14.274565297Z", "buildFinishedOn":"2022-12-15T15:38:14.274565297Z", "reproducible": false,  "reproducible": false, "reproducible": false, "completeness": {  "completeness": { "completeness":{ "parameters": true,  "parameters": true, "parameters": true, "environment": true,  "environment": true, "environment": true, "materials": false  "materials": false "materials": false },  }, }, "https://mobyproject.org/buildkit@v1#metadata": {  "https://mobyproject.org/buildkit@v1#metadata": { "https://mobyproject.org/buildkit@v1#metadata":{ "vcs": {  "vcs": { "vcs":{ "revision": "a9ba846486420e07d30db1107411ac3697ecab68-dirty",  "revision": "a9ba846486420e07d30db1107411ac3697ecab68-dirty", "revision":"a9ba846486420e07d30db1107411ac3697ecab68-dirty", "source": "git@github.com:/.git"  "source": "git@github.com:/.git" "source":"git@github.com:/.git" },  }, }, "source": {  "source": { "source":{ "locations": {  "locations": { "locations":{ "step4": {  "step4": { "step4":{ "locations": [  "locations": [ "locations":[ {  { { "ranges": [  "ranges": [ "ranges":[ { "start": { "line": 5 }, "end": { "line": 5 } },  { "start": { "line": 5 }, "end": { "line": 5 } }, {"start":{"line": 5}, "end":{"line": 5}}, { "start": { "line": 6 }, "end": { "line": 6 } },  { "start": { "line": 6 }, "end": { "line": 6 } }, {"start":{"line": 6}, "end":{"line": 6}}, { "start": { "line": 7 }, "end": { "line": 7 } },  { "start": { "line": 7 }, "end": { "line": 7 } }, {"start":{"line": 7}, "end":{"line": 7}}, { "start": { "line": 8 }, "end": { "line": 8 } }  { "start": { "line": 8 }, "end": { "line": 8 } } {"start":{"line": 8}, "end":{"line": 8}} ]  ] ] }  } } ]  ] ] }  } } },  }, }, "infos": [  "infos": [ "infos":[ {  { { "filename": "Dockerfile",  "filename": "Dockerfile", "filename": "Dockerfile", "data": "RlJPTSBhbHBpbmU6bGF0ZXN0Cg==",  "data": "RlJPTSBhbHBpbmU6bGF0ZXN0Cg==", "data":"RlJPTSBhbHBpbmU6bGF0ZXN0Cg==", "llbDefinition": [  "llbDefinition": [ "llbDefinition":[ {  { { "id": "step0",  "id": "step0", "id": "step0", "op": {  "op": { "op":{ "Op": {  "Op": { "Op":{ "source": {  "source": { "source":{ "identifier": "local://dockerfile",  "identifier": "local://dockerfile", "identifier":"local://dockerfile", "attrs": {  "attrs": { "attrs":{ "local.differ": "none",  "local.differ": "none", "local.differ": "none", "local.followpaths": "[\"Dockerfile\",\"Dockerfile.dockerignore\",\"dockerfile\"]",  "local.followpaths": "[\"Dockerfile\",\"Dockerfile.dockerignore\",\"dockerfile\"]", "local.followpaths":"[\"Dockerfile\",\"Dockerfile.dockerignore\",\"dockerfile\"]", "local.session": "s4j58ngehdal1b5hn7msiqaqe",  "local.session": "s4j58ngehdal1b5hn7msiqaqe", "local.session": "s4j58ngehdal1b5hn7msiqaqe", "local.sharedkeyhint": "dockerfile"  "local.sharedkeyhint": "dockerfile" "local.sharedkeyhint": "dockerfile" }  } } }  } } },  }, }, "constraints": {}  "constraints": {} "constraints":{} }  } } },  }, }, { "id": "step1", "op": { "Op": null }, "inputs": ["step0:0"] }  { "id": "step1", "op": { "Op": null }, "inputs": ["step0:0"] } {"id": "step1", "op":{"Op": null}, "inputs":["step0:0"]} ]  ] ] }  } } ]  ] ] },  }, }, "layers": {  "layers": { "layers":{ "step2:0": [  "step2:0": [ "step2:0":[ [  [ [ {  { { "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",  "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip", "mediaType":"application/vnd.docker.image.rootfs.diff.tar.gzip", "digest": "sha256:261da4162673b93e5c0e7700a3718d40bcc086dbf24b1ec9b54bca0b82300626",  "digest": "sha256:261da4162673b93e5c0e7700a3718d40bcc086dbf24b1ec9b54bca0b82300626", "digest":"sha256:261da4162673b93e5c0e7700a3718d40bcc086dbf24b1ec9b54bca0b82300626", "size": 3259190  "size": 3259190 "size": 3259190 },  }, }, {  { { "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",  "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip", "mediaType":"application/vnd.docker.image.rootfs.diff.tar.gzip", "digest": "sha256:bc729abf26b5aade3c4426d388b5ea6907fe357dec915ac323bb2fa592d6288f",  "digest": "sha256:bc729abf26b5aade3c4426d388b5ea6907fe357dec915ac323bb2fa592d6288f", "digest":"sha256:bc729abf26b5aade3c4426d388b5ea6907fe357dec915ac323bb2fa592d6288f", "size": 286218  "size": 286218 "size": 286218 },  }, }, {  { { "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",  "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip", "mediaType":"application/vnd.docker.image.rootfs.diff.tar.gzip", "digest": "sha256:7f1d6579712341e8062db43195deb2d84f63b0f2d1ed7c3d2074891085ea1b56",  "digest": "sha256:7f1d6579712341e8062db43195deb2d84f63b0f2d1ed7c3d2074891085ea1b56", "digest":"sha256:7f1d6579712341e8062db43195deb2d84f63b0f2d1ed7c3d2074891085ea1b56", "size": 116878653  "size": 116878653 "size": 116878653 },  }, }, {  { { "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip",  "mediaType": "application/vnd.docker.image.rootfs.diff.tar.gzip", "mediaType":"application/vnd.docker.image.rootfs.diff.tar.gzip", "digest": "sha256:652874aefa1343799c619d092ab9280b25f96d97939d5d796437e7288f5599c9",  "digest": "sha256:652874aefa1343799c619d092ab9280b25f96d97939d5d796437e7288f5599c9", "digest":"sha256:652874aefa1343799c619d092ab9280b25f96d97939d5d796437e7288f5599c9", "size": 156  "size": 156 "size": 156 }  } } ]  ] ] ]  ] ] }  } } }  } } }  } } }  } }}}}
```
