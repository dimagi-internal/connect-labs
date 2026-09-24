from storages.backends.s3boto3 import S3Boto3Storage


class StaticRootS3Boto3Storage(S3Boto3Storage):
    location = "static"
    default_acl = "public-read"


class MediaRootS3Boto3Storage(S3Boto3Storage):
    """Uploaded media: private, under media/, read through presigned URLs.

    The bucket is private (labs' exports bucket blocks all public access), so
    an unsigned object URL is a 403. `querystring_auth` makes `url()` sign a
    link that expires after `querystring_expire` seconds -- fetched when a
    person clicks, never stored.
    """

    location = "media"
    file_overwrite = False
    querystring_auth = True
    querystring_expire = 60 * 15
