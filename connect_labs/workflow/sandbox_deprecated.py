"""The render code of every version of the deprecated MBW monitoring templates.

A definition pinned out of the sandbox under ``LABS_WORKFLOW_SANDBOX=all``
(``sandbox.DEPRECATED_UNSANDBOXED``) stays out ONLY while the render code it would draw
is byte-for-byte one of these: labs' own code, as it shipped. Change a pinned copy's
code and it is drawn in the frame like any other workflow. The pin names WHICH records
may stay out; this names WHAT they may run there.

sha256 of the render code with CRLF made LF and outer whitespace stripped
(``sandbox.render_code_digest``), for every committed version of
``templates/mbw_monitoring/template.py`` ``RENDER_CODE`` (v1) and of
``mbw_monitoring_v2_render.js`` / ``mbw_monitoring_v3_render.js`` (removed in #626; the
v2 versions whose code was derived from v1 at import time are not recoverable from
history and are not here). Generated from git history on 2026-10-13; nothing is added
to it, since no new version of a deprecated template is ever written.
"""

DEPRECATED_RENDER_DIGESTS: frozenset[str] = frozenset(
    {
        "072bb833d554e9b1ce2a33c0b19a0b93b03b152e64492103fb499cd72a94d1a4",  # v1 @ 8af7aef81
        "082810a7db86a0972027cca4a34ff9f1ef134517e2a6a52807134f33c82aacc9",  # v1 @ 1d2e4c692
        "15c94de4e77f35c660becddbf1cd01ba63be00d73fad992e2fd51c7b7eed22f9",  # v1 @ 87ad23c49
        "2542e7a56276857e6087af2bd49458f258d5319f82a40c32692c0f0e8bb3852e",  # v1 @ 5a32425bf
        "2cb2e320dcb238d38cc21499c955c86f8b3bf2fb996a3bfb10f8c3c80c673a63",  # v1 @ c68d4308c
        "302a43ee5e91308de35c4970e76bafa787fe5765234a61d2eee8bdb6071716d1",  # v1 @ 373afec3e
        "57cdcb1f95fe870031374484b533d4c031ad1ac4136e392dd59ef4d7f8f27bda",  # v1 @ b62e92928
        "5f2a767c89dd653620c8c44e6b1f619d44b88d0ff9f2ea54fc8cc999e8d06006",  # v1 @ 92cdd6421
        "5f64f9f6ddb5713072130d3a963bba2bc0d2202bf2a7113f05afd02a8886c026",  # v1 @ b9f1a3604
        "6a4254f48972992f4325240e13f6f0934e87674fc354ef49281e5104dd4bcc49",  # v1 @ 52b5cd5d0
        "73c76deee61b6c7b96a8178aa8fe220dbd64383cda8cb2feb69b523d9ed9a84b",  # v1 @ 7697bde4d
        "77b0672da98cc7a9d5a359396911028865e539dadfd8fa789ed24227d48ddcde",  # v1 @ 2dfa35f24
        "7af632a9057f5004ed8b7ce873ab1464d844d782df06a48751abc33e1a45c074",  # v1 @ 9824a4f02
        "80d15dd4f1b20c3619410bce2ac99f0985dc6d417e55a422f9e29e68a83efa72",  # v1 @ e01733fbf
        "875fb7fd13206a2421e5e4746b82495097d65ddbfee2a1d5602107ad6dbe2c9e",  # v1 @ 77c0ba31c
        "879faf9230b172469a0ddfaa508de2f462875b9bcf3ff614a28dd9de8626ccdb",  # v1 @ 28fa86aca
        "8bac0c85a864e37a966d38c2be2b0542378a93c9bee8509d84dcd48c3d158c24",  # v1 @ ecbac89a9
        "a1427b70b9124309d70469c3ee74afa15ab863fca1f0b2af2aff9a75f87418f2",  # v1 @ 943f0eb08
        "a22d2699a85400fe9d52abf46bdb6e99cdb51587d6959af5277881e6a39e48f8",  # v1 @ 4398f9821
        "a568fc9cfc353c894d34a695aba35896cdc2033d515b299f97fe70743539c0c8",  # v1 @ 5fcaec086
        "a8d6e7c3997833eaf3ce3e7dbe1bbe951d6a042b6998ee44c121a3c492af38c7",  # v1 @ 4297c9dfa
        "a91435b1f3e4c83841f44635ca99d3b69bcf3b0eec7e66a315c3fbab0266b4d1",  # v1 @ 57c9f0c51
        "a99180b94a76867221a70cc01b105d6a445ec64965dfd854cbb196b2765fd72d",  # v1 @ a68877178
        "bb6b3b3bdd2d34582b34f25c0475edfa74f90f25dcbf8a119dcad961229021ce",  # v1 @ d0a01b0d6
        "bf4ecd8bad2d023fe8617e1c13c814b3af5efc7e83dcb3394d4ed8c134ff68b7",  # v1 @ dc81df01e
        "c4d4f3613bba2382273f587a3af081558277c88a2e611cb404eeb2a323f3ac58",  # v1 @ 5b59f19ee
        "c6df76e2e5d5bae072c32e749b31413d544384547337892fcda77e88d46a9db8",  # v1 @ dcec805c6
        "c7f5ad9733e6bf9af06faeea49a22648547b6b63c49855519f13e61fc23ae351",  # v1 @ 1039a387f
        "c935d87b677607a0cbdd9ff0c3eaf7cb1c6a1e58179f8a48e0f35c44303d64e3",  # v1 @ 32c0809de
        "d1eaa64322b5985fe8ee134931bea21b368f64e0a8c47f6af74dc875d1feead9",  # v1 @ d55e8d5c1
        "e93739f36537f5a9d158c52c6ada2bd7bcd2f5fde368dd7ce8b1ee5ed845e89f",  # v1 @ 042991122
        "ec7fb5093942c33d99758d00ef01f0b1a7251f450fbcd3feca612c62fc73924c",  # v1 @ 8bbaef0c9
        "f99b06a13ffcb05bb6c32e5e9847fb98b882312e52afe940f9cd4e8204091cbf",  # v1 @ aa2067b2f
        "fe028910401ff846307718a37894d30741d5b6c555eaca525fa7b2b386304577",  # v1 @ 6d680e627
        "14a5b8da8d8eff5f43e2cfe2ad3c883215c3808511d16290bfcd2ed1794deaa9",  # v2 @ 55190d397
        "4cda73b59346ab2c94b2e529be3385f37167343afd5ca9db57560abe0b3917ab",  # v2 @ 3c03bace9
        "71616f6b7570ae01ac2c112ad0766e9dfd6f55fde2ebbdaba2505d96e776ff57",  # v2 @ eab6d1df9
        "b451cf62a66f0387b781f3c94644d8af2937fc8660fd55acf9f602cf60da6401",  # v2 @ 56f382d32
        "c5f816ac56e1c2aa9b895c29e615225e61cadf04016dd8e2079e052c772a1955",  # v2 @ 85c02ff7d
        "f223f4be813931e261d173010bd21524a7b94df47c27e20788dc69b89087512e",  # v2 @ c900bc771
        "ffc60966f0adedb3be209bcb6d9dbcc5406ad3c5024298521ebb7dd8743b15f8",  # v2 @ dda4db8ad
        "04e7f0179da5ffef67cc339012d525b8ec1e17c5e0dee1b048d64eaf03b9d7c0",  # v3 @ 477f3403c
        "1e049ff53119e82f638e2fffb3e94bb775e5dd5263f9cc2a543b374fdac44121",  # v3 @ 196aa14c1
        "6e753e304e049d681433c7271a60dfb0393dc01c367f0be528e1fcc8c6f79052",  # v3 @ 48da07fba
        "aa3243eb2e89074ed4bf71d58f39c347833d9a8609330d35e2251db1c0d59989",  # v3 @ fb33c567e
        "b05a6aabc56d53937f5e45ad2744b99596759cb6b8cc85df3af2e05cee5660e1",  # v3 @ 6dfd978fa
        "b2231c6e42695451d38b73bca47bc9ca8c917c2f958dee9a34822cd87b1030c0",  # v3 @ 31809bf6b
        "bcb7c55da3b4304e08ec3c0f99ff2d77316844c2f55ffad0e3fb6894feb439ef",  # v3 @ 9de390357
        "e2a6beeadc3e7117aaddc98bacb81084d81ce75af40a02424effde48c7eb80d9",  # v3 @ 18977bd42
        "e9effbd4bff377f71f51ee7f5d84a8ce41f714afd9439aa16a3b32057a97fa7d",  # v3 @ 12f8ab087
    }
)
