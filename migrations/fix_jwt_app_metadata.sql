-- ============================================================================
-- FIX: Add company_id to JWT app_metadata for all users
-- ============================================================================
--
-- PROBLEM: Backend expects company_id in JWT app_metadata, but Supabase
-- doesn't automatically populate this from company_users table.
--
-- SOLUTION: Update auth.users.raw_app_meta_data to include company_id
-- for all users in company_users table.
--
-- This SQL:
-- 1. Backfills company_id into app_metadata for existing users
-- 2. Creates a trigger to automatically update JWT when user joins company
-- ============================================================================

-- Step 1: Backfill company_id into app_metadata for all existing users
UPDATE auth.users
SET raw_app_meta_data =
  COALESCE(raw_app_meta_data, '{}'::jsonb) ||
  jsonb_build_object('company_id', cu.company_id::text, 'role', cu.role)
FROM company_users cu
WHERE auth.users.id = cu.user_id
  AND cu.is_active = TRUE;

-- Verify the update
SELECT
  au.id,
  au.email,
  au.raw_app_meta_data->>'company_id' as jwt_company_id,
  cu.company_id as db_company_id,
  cu.role
FROM auth.users au
LEFT JOIN company_users cu ON cu.user_id = au.id
ORDER BY au.created_at DESC
LIMIT 10;

-- Step 2: Create or replace trigger function to update JWT metadata
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS TRIGGER AS $$
BEGIN
  -- Insert into company_users with default company_id
  INSERT INTO public.company_users (
    user_id,
    company_id,
    email,
    full_name,
    role,
    is_active
  )
  VALUES (
    NEW.id,
    '0eb96b39-c31d-44b6-af44-39c9cc2b6383'::UUID,  -- Test company ID
    NEW.email,
    COALESCE(NEW.raw_user_meta_data->>'full_name', NEW.email),
    'member',
    TRUE
  )
  ON CONFLICT (user_id, company_id) DO NOTHING;

  -- CRITICAL: Update auth.users.raw_app_meta_data to include company_id
  -- This ensures the JWT includes company_id in app_metadata
  UPDATE auth.users
  SET raw_app_meta_data =
    COALESCE(raw_app_meta_data, '{}'::jsonb) ||
    jsonb_build_object(
      'company_id', '0eb96b39-c31d-44b6-af44-39c9cc2b6383'::text,
      'role', 'member'
    )
  WHERE id = NEW.id;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Recreate the trigger (drop first to avoid conflicts)
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION public.handle_new_user();

-- Step 3: Also create trigger to update JWT when user is added to company_users
-- (in case users are added to company_users manually after signup)
CREATE OR REPLACE FUNCTION public.sync_user_jwt_metadata()
RETURNS TRIGGER AS $$
BEGIN
  -- Update auth.users.raw_app_meta_data when company_users changes
  UPDATE auth.users
  SET raw_app_meta_data =
    COALESCE(raw_app_meta_data, '{}'::jsonb) ||
    jsonb_build_object(
      'company_id', NEW.company_id::text,
      'role', NEW.role
    )
  WHERE id = NEW.user_id;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

DROP TRIGGER IF EXISTS on_company_user_updated ON company_users;
CREATE TRIGGER on_company_user_updated
  AFTER INSERT OR UPDATE ON company_users
  FOR EACH ROW
  EXECUTE FUNCTION public.sync_user_jwt_metadata();

-- Success message
DO $$
BEGIN
  RAISE NOTICE '✅ JWT metadata fix applied successfully!';
  RAISE NOTICE '⚠️  IMPORTANT: Users must LOGOUT and LOGIN again to get new JWT with company_id';
  RAISE NOTICE '📝 Next step: Ask users to logout/login or wait for JWT to expire';
END $$;
