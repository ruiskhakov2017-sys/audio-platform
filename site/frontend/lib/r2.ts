import { S3Client } from '@aws-sdk/client-s3';

let client: S3Client | null = null;

function getR2Client(): S3Client | null {
  const accountId = process.env.R2_ACCOUNT_ID;
  const accessKeyId = process.env.R2_ACCESS_KEY_ID;
  const secretAccessKey = process.env.R2_SECRET_ACCESS_KEY;
  if (!accountId || !accessKeyId || !secretAccessKey) return null;
  if (!client) {
    client = new S3Client({
      region: 'auto',
      endpoint: `https://${accountId}.r2.cloudflarestorage.com`,
      credentials: {
        accessKeyId,
        secretAccessKey,
      },
    });
  }
  return client;
}

export const r2 = getR2Client();
export const R2_BUCKET = process.env.R2_BUCKET_NAME || process.env.R2_BUCKET || 'stories';
export const R2_PUBLIC_URL = process.env.R2_PUBLIC_URL || '';
