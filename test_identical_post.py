import unittest
from posting_adapter import new_identical_bubble


class IdenticalPostTests(unittest.TestCase):
    def test_new_bubble_requires_old_bubble_still_present(self):
        before=dict(last_message_id='old',visible_message_ids=['a','old'])
        after=dict(last_message_id='new',visible_message_ids=['old','new'])
        self.assertTrue(new_identical_bubble(before,after))
        for candidate in [before,dict(last_message_id='new',visible_message_ids=['new']),
                          dict(last_message_id='a',visible_message_ids=['old','a']),{},
                          dict(last_message_id='new',visible_message_ids=['old'])]:
            self.assertFalse(new_identical_bubble(before,candidate))


if __name__=='__main__':unittest.main()
